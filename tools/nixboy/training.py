"""Durable learning attempts and a bridge-owned, shell-free terminal menu.

The game owns skills. This module owns exercise state and trusted completion
receipts. Learner output and legacy helper events never enter this reward path.
"""

import json
import logging
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import textwrap

from protocol import (
    ERROR, OUTPUT, READY, TRAIN_REWARD, TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET, TRAIN_ABANDON,
    MAX_CLAIMS, SKILL_NAMES, ProtocolError, encode_training_reward, training_context, validate_claims,
)

LOG = logging.getLogger("nixboy.training")
TRACKS = dict(enumerate(SKILL_NAMES))
STATE_VERSION = 4
IDLE_SECONDS = 24 * 60 * 60


def default_state_dir():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "NixBoy/training"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "nixboy/training"


class AttemptManager:
    """One durable attempt per profile; environments outlive frontend sockets."""

    def __init__(self, state_dir, client_factory, catalog=None, environment_factory=None,
                 cleanup=None):
        if catalog is None:
            from challenge_content import Catalog
            catalog = Catalog()
        if environment_factory is None:
            from challenge_env import ComposeEnvironment, cleanup_owned
            environment_factory = ComposeEnvironment
            cleanup = cleanup_owned
        self.catalog = catalog
        self.environment_factory = environment_factory
        self.client_factory = client_factory
        self.cleanup = cleanup
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.state_dir / "training.json"
        self.lock = threading.RLock()
        self.operations = {}
        self.environments = {}
        self.contexts = {}
        self.attached = set()
        self.closed = False
        self.reconciled = False
        self.reconcile_lock = threading.Lock()
        self.data = {"version": STATE_VERSION, "profiles": {}}
        if self.path.exists():
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("training state exceeds size limit")
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self._migrate_state()
            self._validate_state()
        for profile in self.data["profiles"].values():
            attempt = profile.get("attempt")
            if attempt:
                attempt["status"] = "needs_reset"
        self._save()

    def _migrate_state(self):
        if not isinstance(self.data, dict) or self.data.get("version") not in (1, 2, 3):
            return
        version = self.data["version"]
        profiles = self.data.get("profiles")
        if not isinstance(profiles, dict):
            raise ValueError("invalid legacy training profiles")
        for profile in profiles.values():
            if not isinstance(profile, dict):
                raise ValueError("invalid legacy training profile")
            receipts = profile.get("receipts", {})
            if not isinstance(receipts, dict):
                raise ValueError("invalid legacy completion receipts")
            # Historical rewards were always five. Never consult today's catalog.
            upgraded = {key: {"receipt": receipt, "points": 5} for key, receipt in receipts.items()}
            validate_claims(upgraded)
            if version in (1, 2):
                progress = profile.get("earned" if version == 1 else "claimed", {})
                if not isinstance(progress, dict):
                    raise ValueError("invalid legacy training progress")
                expected = set()
                for skill in (3, 12):
                    value = progress.get(str(skill), 0)
                    if type(value) is not int or not 0 <= value <= (2 if version == 1 else 3):
                        raise ValueError("invalid legacy training progress")
                    mask = (1 << value) - 1 if version == 1 else value
                    for level in (1, 2):
                        if mask & (1 << (level - 1)):
                            expected.add(f"{skill}:{level}")
                if set(receipts) != expected:
                    raise ValueError("legacy completion receipts do not match its recorded progress")
            profile["receipts"] = upgraded
            acknowledged = profile.get("acknowledged")
            if isinstance(acknowledged, dict):
                profile["acknowledged"] = {key: {"receipt": receipt, "points": 5}
                                            for key, receipt in acknowledged.items()}
            profile.pop("earned", None)
            profile.pop("claimed", None)
        self.data["version"] = STATE_VERSION

    def _validate_state(self):
        if not isinstance(self.data, dict) or self.data.get("version") != STATE_VERSION \
                or not isinstance(self.data.get("profiles"), dict):
            raise ValueError("unsupported training state; preserve it for recovery")
        for key, profile in self.data["profiles"].items():
            if len(key) != 32 or any(ch not in "0123456789abcdef" for ch in key) \
                    or not isinstance(profile, dict):
                raise ValueError("invalid training profile state")
            validate_claims(profile.get("receipts", {}))
            if not isinstance(profile.get("queues", {}), dict):
                raise ValueError("invalid challenge queue")

    def _save(self):
        """Replace one fsynced snapshot; a pass is committed before transmission."""
        temporary = self.path.with_suffix(".tmp")
        encoded = json.dumps(self.data, sort_keys=True, separators=(",", ":"))
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        if os.name != "nt":
            directory = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def _profile(self, identity):
        return self.data["profiles"].setdefault(identity, {
            "receipts": {},
            "queues": {}, "attempt": None, "updated": time.time(),
        })

    def snapshot(self, identity):
        with self.lock:
            profile = self._profile(identity)
            profile["updated"] = time.time()
            return json.loads(json.dumps(profile))

    def _operation(self, identity):
        with self.lock:
            return self.operations.setdefault(identity, threading.Lock())

    def current_level(self, identity, skill):
        with self.lock:
            value = self.contexts.get(identity, {}).get("skill." + TRACKS[skill])
            return self.catalog.level_for_value(skill, value)

    def _eligible(self, identity, skill, level, mode):
        if self.current_level(identity, skill) != level:
            raise ValueError("The current skill value has no matching exercise at this level; abandon and reselect")
        if mode == "assessment" and f"{skill}:{level}" in self._profile(identity)["receipts"]:
            raise ValueError("This skill band's reward is already claimed; practice remains available")
        if mode == "assessment" and len(self._profile(identity)["receipts"]) >= MAX_CLAIMS:
            raise ValueError("This character has reached the supported training receipt limit")

    def attach_frontend(self, identity):
        with self.lock:
            self.attached.add(identity)
            self._profile(identity)["updated"] = time.time()

    def detach_frontend(self, identity):
        with self.lock:
            self.attached.discard(identity)
            self._profile(identity)["updated"] = time.time()
            self._save()

    def observe(self, identity, claimed, values):
        """Import exact game receipts; the receipt map is the completion ledger."""
        validate_claims(claimed)
        with self.lock:
            profile = self._profile(identity)
            receipts = dict(profile["receipts"])
            recovered = list(profile.get("recovered_from_game", []))
            for key, receipt in claimed.items():
                existing = receipts.get(key)
                if existing and existing != receipt:
                    raise ProtocolError("saved reward receipt conflicts with training state")
                if not existing:
                    receipts[key] = dict(receipt)
                    recovered.append(key)
            validate_claims(receipts)
            profile["receipts"] = receipts
            self.contexts[identity] = dict(values)
            if recovered:
                profile["recovered_from_game"] = recovered
            profile["updated"] = time.time()
            self._save()

    def _pool(self, profile, skill, level):
        key = f"{skill}:{level}"
        authored = self.catalog.pool(skill, level)
        if not authored:
            raise ValueError("No authored challenge at this level")
        stored = profile["queues"].get(key)
        if stored is None:
            stored = list(authored)
            secrets.SystemRandom().shuffle(stored)
            profile["queues"][key] = stored
        elif set(stored) != set(authored) or len(stored) != len(authored):
            # Content revisions retain surviving order and shuffle only new IDs.
            stored = [item for item in stored if item in authored]
            added = [item for item in authored if item not in stored]
            secrets.SystemRandom().shuffle(added)
            stored.extend(added)
            profile["queues"][key] = stored
        return stored

    def candidate(self, identity, skill, level):
        with self.lock:
            profile = self._profile(identity)
            pool = self._pool(profile, skill, level)
            self._save()
            return self.catalog.get(pool[0])

    def select(self, identity, skill, level, mode="assessment"):
        if skill not in self.catalog.tracks or level not in self.catalog.tracks[skill]["levels"] \
                or mode not in ("assessment", "practice"):
            raise ValueError("Unsupported training selection")
        with self.lock:
            if self.closed:
                raise RuntimeError("Training service is stopping")
            profile = self._profile(identity)
            if profile.get("attempt"):
                raise ValueError("Resume or abandon the active exercise first")
            self._eligible(identity, skill, level, mode)
            challenge = self._pool(profile, skill, level)[0]
            spec = self.catalog.get(challenge)
            seed = secrets.randbits(63)
            attempt = {"id": secrets.token_hex(16), "skill": skill, "level": level,
                       "challenge": challenge, "seed": seed, "generation": 0,
                       "challenge_version": getattr(spec, "version", 1),
                       "content_digest": self.catalog.digest(challenge),
                       "reward_points": self.catalog.tracks[skill]["levels"][level].get("reward_points", 5),
                       "params": self.catalog.instantiate(challenge, seed),
                       "mode": mode, "status": "selected", "updated": time.time()}
            profile["attempt"] = attempt
            profile["updated"] = time.time()
            self._save()
            return dict(attempt)

    def reconcile_resources(self):
        # Lazy: entering a menu or malformed requests must not contact Docker.
        with self.reconcile_lock:
            if self.reconciled:
                return
            if self.cleanup:
                client = self.client_factory()
                try:
                    self.cleanup(client, self.state_dir)
                finally:
                    client.close()
            self.reconciled = True

    def prepare(self, identity):
        with self._operation(identity):
            self.reconcile_resources()
            with self.lock:
                if self.closed:
                    raise RuntimeError("Training service is stopping")
                profile = self._profile(identity)
                attempt = profile.get("attempt")
                if not attempt:
                    raise ValueError("No active exercise")
                self._check_content(attempt)
                if identity in self.environments:
                    return self.environments[identity].container_id
                if attempt["status"] == "needs_reset":
                    attempt["generation"] += 1
                attempt["status"] = "provisioning"
                self._save()
                spec = self.catalog.get(attempt["challenge"])
                if getattr(spec, "version", 1) != attempt.get("challenge_version", 1):
                    raise ValueError("Exercise content changed; abandon this attempt and select a new one")
                parameters = dict(attempt)
            client = self.client_factory()
            environment = None
            try:
                environment = self.environment_factory(
                    client, spec, parameters["id"], parameters["seed"], parameters["generation"],
                    self.state_dir, params=parameters["params"], content_root=self.catalog.root,
                    training_label=self._training_label(parameters))
                container_id = environment.start()
                with self.lock:
                    if self.closed:
                        raise RuntimeError("Training service is stopping")
                    self.environments[identity] = environment
                    attempt["status"] = "active"
                    attempt["updated"] = time.time()
                    self._save()
                return container_id
            except Exception:
                if environment:
                    try:
                        environment.close()
                    except Exception:
                        LOG.exception("Training partial startup cleanup failed")
                else:
                    client.close()
                with self.lock:
                    self.environments.pop(identity, None)
                    attempt["status"] = "needs_reset"
                    self._save()
                raise

    def _training_label(self, attempt):
        track = self.catalog.tracks[attempt["skill"]]
        band = track["levels"][attempt["level"]]
        reward = attempt["reward_points"]
        return f"{track['knowledge_title']} / {band['title']} - level {attempt['level']} ({track['title']}); assessment reward +{reward}"

    def _check_content(self, attempt):
        if not attempt.get("content_digest") or "reward_points" not in attempt:
            raise ValueError("This older attempt has no content fingerprint; abandon and reselect. Earned progress is preserved")
        try:
            digest = self.catalog.digest(attempt["challenge"])
        except (OSError, ValueError) as exc:
            raise ValueError("Exercise content is unavailable; abandon and reselect. Earned progress is preserved") from exc
        band = self.catalog.tracks[attempt["skill"]]["levels"][attempt["level"]]
        if type(attempt["reward_points"]) is not int or not 1 <= attempt["reward_points"] <= 300 \
                or attempt["reward_points"] != band.get("reward_points", 5) \
                or digest != attempt["content_digest"]:
            raise ValueError("Exercise content changed; abandon and reselect. Earned progress is preserved")

    def _remove_environment(self, identity):
        with self.lock:
            environment = self.environments.pop(identity, None)
        if environment:
            environment.close()

    def _rotate(self, profile, attempt):
        pool = self._pool(profile, attempt["skill"], attempt["level"])
        challenge = attempt["challenge"]
        if challenge in pool:
            pool.remove(challenge)
            pool.append(challenge)

    def check(self, identity):
        with self._operation(identity):
            with self.lock:
                profile = self._profile(identity)
                attempt = profile.get("attempt")
                environment = self.environments.get(identity)
                if not attempt or not environment:
                    return {"status": "error", "feedback": "Resume the exercise before checking."}
                try:
                    self._check_content(attempt)
                except ValueError as exc:
                    return {"status": "error", "feedback": str(exc)}
                if self.current_level(identity, attempt["skill"]) != attempt["level"]:
                    return {"status": "error", "feedback": "Your skill value changed bands. Files are preserved; abandon and select your current band."}
                attempt["status"] = "checking"
                self._save()
            try:
                result = environment.check()
                if not isinstance(result, dict) or result.get("status") not in ("passed", "incomplete", "error"):
                    raise ValueError("Validator returned an invalid result")
            except Exception:
                LOG.exception("Training validator failed for %s", attempt["id"])
                result = {"status": "error", "feedback": "Validator/environment error; your attempt is preserved."}
            with self.lock:
                if self.closed:
                    attempt["status"] = "needs_reset"
                    self._save()
                    self._remove_environment(identity)
                    return {"status": "error", "feedback": "Training service is stopping; attempt preserved."}
                attempt["status"] = "active"
                attempt["updated"] = time.time()
                if result["status"] == "error":
                    self._save()
                    return result
                try:
                    self._check_content(attempt)
                except ValueError as exc:
                    self._save()
                    return {"status": "error", "feedback": str(exc)}
                if self.current_level(identity, attempt["skill"]) != attempt["level"]:
                    self._save()
                    return {"status": "error", "feedback": "Your skill value changed bands during assessment. Files are preserved; abandon and reselect."}
                receipt = None
                if result["status"] == "passed" and attempt["mode"] == "assessment":
                    skill, level = attempt["skill"], attempt["level"]
                    key = f"{skill}:{level}"
                    if key in profile["receipts"] or len(profile["receipts"]) >= MAX_CLAIMS:
                        self._save()
                        return {"status": "error", "feedback": "This band's reward was already claimed. Attempt preserved; abandon to continue."}
                    receipt = secrets.token_hex(16)
                    profile["receipts"][key] = {"receipt": receipt, "points": attempt["reward_points"]}
                    # Preserve evidence even after the disposable environment is removed.
                    profile.setdefault("passes", {})[key] = {
                        "attempt": attempt["id"], "challenge": attempt["challenge"],
                        "challenge_version": attempt.get("challenge_version", 1),
                        "seed": attempt["seed"], "generation": attempt["generation"],
                        "reward_points": attempt["reward_points"],
                    }
                self._rotate(profile, attempt)
                profile["attempt"] = None
                profile["updated"] = time.time()
                self._save()  # Receipt durable before the caller can emit TRAIN_REWARD.
            try:
                self._remove_environment(identity)
            except Exception:
                LOG.exception("Completed training cleanup failed; restart reconciliation will retry")
            result = dict(result)
            result["rewarded"] = receipt is not None
            if receipt is not None:
                result["reward_points"] = attempt["reward_points"]
            return result

    def reset(self, identity):
        with self._operation(identity):
            with self.lock:
                attempt = self._profile(identity).get("attempt")
                if not attempt:
                    raise ValueError("No active exercise to reset")
            self._remove_environment(identity)
            with self.lock:
                attempt["status"] = "needs_reset"
                self._save()
        return self.prepare(identity)

    def abandon(self, identity):
        with self._operation(identity):
            try:
                self._remove_environment(identity)
            finally:
                with self.lock:
                    self._profile(identity)["attempt"] = None
                    self._save()

    def pending_rewards(self, identity, claimed):
        validate_claims(claimed)
        with self.lock:
            profile = self._profile(identity)
            result = []
            for key, receipt in sorted(profile["receipts"].items(), key=lambda item: tuple(map(int, item[0].split(":")))):
                if key in claimed:
                    if claimed[key] != receipt:
                        raise ProtocolError("game receipt conflicts with earned completion")
                    continue
                skill, level = map(int, key.split(":"))
                result.append((skill, level, receipt["points"], receipt["receipt"]))
            return result

    def acknowledge(self, identity, skill, level, points, receipt, accepted):
        claim = {"receipt": receipt, "points": points}
        validate_claims({f"{skill}:{level}": claim})
        with self.lock:
            expected = self._profile(identity)["receipts"].get(f"{skill}:{level}")
            if expected != claim:
                raise ProtocolError("acknowledgment does not match an earned receipt")
            if accepted:
                self._profile(identity).setdefault("acknowledged", {})[f"{skill}:{level}"] = claim
                self._save()

    def expire(self):
        """Expire detached environments; preserve their seeded exercise and progress."""
        now = time.time()
        with self.lock:
            identities = [identity for identity in self.environments
                          if identity not in self.attached
                          and now - self._profile(identity)["updated"] > IDLE_SECONDS]
        for identity in identities:
            operation = self._operation(identity)
            if not operation.acquire(blocking=False):
                continue
            try:
                self._remove_environment(identity)
                with self.lock:
                    attempt = self._profile(identity).get("attempt")
                    if attempt:
                        attempt["status"] = "needs_reset"
                    self._save()
            except Exception:
                LOG.exception("Expired training cleanup failed")
            finally:
                operation.release()

    def close(self):
        with self.lock:
            self.closed = True
            # Provisioning/checking may hold an operation before an environment
            # has been registered. Wait for those workers before process exit.
            identities = list(set(self.environments) | set(self.operations))
            for profile in self.data["profiles"].values():
                if profile.get("attempt"):
                    profile["attempt"]["status"] = "needs_reset"
            self._save()
        deadline = time.monotonic() + 70
        for identity in identities:
            operation = self._operation(identity)
            if not operation.acquire(timeout=max(0, deadline - time.monotonic())):
                LOG.error("Training operation still stopping; recovery cleanup will reconcile its owned resources")
                continue
            try:
                self._remove_environment(identity)
            except Exception:
                LOG.exception("Training shutdown cleanup failed")
            finally:
                operation.release()


class TrainingSession:
    """A restricted text menu and detachable challenge terminal, never a host shell."""

    def __init__(self, manager, send, shell_factory):
        self.manager, self.send, self.shell_factory = manager, send, shell_factory
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.events = queue.Queue(maxsize=16)
        self.shell = None
        self.shell_token = None
        self.job = None
        self.mode = "hub"
        self.line = bytearray()
        self.selected = None
        self.last_reward = 0
        self.hub_page = 0

    def start(self, rows, cols, context, progress):
        self.rows, self.cols = rows, cols
        self.values = dict(context)
        self.identity, self.skills = training_context(context)
        self.claimed = dict(validate_claims(progress))
        self._observe(self.identity, self.claimed, context)
        self.manager.attach_frontend(self.identity)
        self.send(READY)
        self.hub()
        self.rewards()

    def _observe(self, identity, claimed, values):
        try:
            self.manager.observe(identity, claimed, values)
        except ProtocolError as exc:
            self.send(ERROR, ("Training progress recovery failed: " + str(exc)).encode("utf-8")[:4096])
            raise

    def output(self, message, clear=False):
        if self.closed.is_set():
            return
        # Text comes only from trusted catalog/validator or game-controlled integers.
        clean = str(message).replace("\x1b", "?").replace("\0", "?")
        data = (("\x1b[?1049l\x1bc\x1b[2J\x1b[H" if clear else "") + clean.replace("\r\n", "\n").replace("\n", "\r\n"))
        self.send(OUTPUT, data.encode("utf-8")[:65536])

    def hub(self, notice=""):
        self.notice = str(notice)[:1000]
        self.mode, self.selected = "hub", None
        self.line.clear()
        state = self.manager.snapshot(self.identity)
        tracks = list(self.manager.catalog.tracks)
        size = max(1, (self.rows - 13) // 2)
        pages = max(1, (len(tracks) + size - 1) // size)
        self.hub_page = min(self.hub_page, pages - 1)
        lines = ["nix-boy", "Training hub - knowledge powers your character.", ""]
        width = max(20, self.cols - 1)
        for index in range(self.hub_page * size, min(len(tracks), (self.hub_page + 1) * size)):
            skill = tracks[index]
            track = self.manager.catalog.tracks[skill]
            value = self.skills[skill]
            level = self.manager.current_level(self.identity, skill)
            band = track["levels"].get(level)
            completed = sum(f"{skill}:{number}" in state["receipts"] for number in track["levels"])
            lines.append(f"[{index + 1}] {track['title']} {value}% | {track['knowledge_title']}"[:width])
            availability = (f"level {level} ({band['min_skill']}-{band['max_skill']}%): {band['title']} [+{band.get('reward_points', 5)}]" if band else
                            "no authored challenges at this value")
            lines.append(f"    {availability}; completed {completed}/{len(track['levels'])}"[:width])
        if pages > 1:
            lines.append(f"Page {self.hub_page + 1}/{pages} | [N] Next | [B] Back")
        if state.get("attempt"):
            attempt = state["attempt"]
            lines.append(f"[R] Resume {SKILL_NAMES[attempt['skill']]} level {attempt['level']} ({attempt['mode']})"[:width])
        lines.extend(["F5 Check | F6 Hub | F7 Reset | F8 Abandon", "F10 Close (keeps an active exercise for later)"])
        if self.notice:
            lines.extend(textwrap.wrap(self.notice, width)[:3])
        lines.append("Choose a number or R, then Enter: ")
        self.output("\n".join(lines), clear=True)

    def track_menu(self, skill):
        state = self.manager.snapshot(self.identity)
        if state.get("attempt"):
            self.hub("An exercise is active. Resume it or use F8 to abandon it first.")
            return
        level = self.manager.current_level(self.identity, skill)
        track = self.manager.catalog.tracks[skill]
        if level is None:
            self.hub(f"No authored challenges for {track['title']} at {self.skills[skill]}%.")
            return
        self.selected = (skill, level)
        self.mode = "track"
        spec = self.manager.candidate(self.identity, skill, level)
        band = track["levels"][level]
        finished = f"{skill}:{level}" in state["receipts"]
        self.output(f"{track['title']} {self.skills[skill]}% - level {level} "
                    f"({band['min_skill']}-{band['max_skill']}%)\n"
                    f"{track['knowledge_title']} / {band['title']}\n{spec.title}\n\n"
                    "[P] Practice (no character reward)\n"
                    + ("This band's reward is claimed. Practice remains available.\n" if finished else
                       f"[A] Assessment (earn +{band.get('reward_points', 5)} skill on success)\n")
                    + "[H] Return to hub\nChoose a letter, then Enter: ", clear=True)

    def input(self, data):
        with self.lock:
            if self.shell and self.mode == "challenge":
                self.shell.input(data)
                return
        if self.job and self.job.is_alive():
            return
        for char in data:
            if char in (10, 13):
                if not self.line:
                    continue
                value = self.line.decode("ascii").strip().lower()
                self.line.clear()
                self._choice(value)
            elif char in (8, 127):
                if self.line:
                    self.line.pop()
                    self.output("\b \b")
            elif 32 <= char <= 126 and len(self.line) < 32:
                self.line.append(char)
                self.output(chr(char))

    def _choice(self, choice):
        if self.mode == "hub":
            if choice.isdigit() and 1 <= int(choice) <= len(self.manager.catalog.tracks):
                self.track_menu(list(self.manager.catalog.tracks)[int(choice) - 1])
            elif choice in ("n", "b"):
                self.hub_page = max(0, self.hub_page + (1 if choice == "n" else -1))
                self.hub()
            elif choice == "r":
                self._launch(self.manager.prepare)
            else:
                self.hub("Choose an authored track number, or R to resume an active exercise.")
        elif self.mode == "track":
            skill, level = self.selected
            if choice == "h":
                self.hub()
            elif choice in ("p", "a"):
                try:
                    self.manager.select(self.identity, skill, level,
                                        "practice" if choice == "p" else "assessment")
                except ValueError as exc:
                    self.hub(str(exc))
                    return
                self._launch(self.manager.prepare)
            else:
                self.output("\nChoose P, A, or H, then Enter: ")

    def _detach(self):
        with self.lock:
            shell, self.shell = self.shell, None
            self.shell_token = None
            self.mode = "hub"
        if shell:
            shell.close()

    def _launch(self, operation):
        self._detach()
        self.output("Preparing exercise; F10 can close this screen.\n", clear=True)

        def work():
            container_id = operation(self.identity)
            if self.closed.is_set():
                return None
            state = self.manager.snapshot(self.identity)
            attempt = state.get("attempt")
            if not attempt:
                return None
            spec = self.manager.catalog.get(attempt["challenge"])
            instructions = spec.instructions.format(**attempt['params'])
            lines = textwrap.wrap(instructions, max(20, self.cols - 2))
            visible = "\n".join(lines[:max(1, self.rows - 8)])
            track = self.manager.catalog.tracks[attempt["skill"]]
            band = track["levels"][attempt["level"]]
            self.output(f"{self.manager._training_label(attempt)}\n{spec.title}\n{visible}\nFull task: cat ~/CHALLENGE.txt\n\nF5 Check | F6 Hub | F7 Reset | F8 Abandon\n")
            token = object()
            shell = self.shell_factory(container_id, self.send,
                                       lambda: self._event("shell_exit", token))
            with self.lock:
                if self.closed.is_set():
                    shell.close()
                    return None
                self.shell = shell
                self.shell_token = token
                self.mode = "challenge"
            try:
                shell.start(self.rows, self.cols, self.values)
            except Exception:
                self._detach()
                raise
            return "attached"

        self._job(work, "launch")

    def _job(self, work, name):
        if self.job and self.job.is_alive():
            self.output("\nPlease wait for the current operation.\n")
            return

        def run():
            try:
                self._event(name, work())
            except Exception as exc:
                LOG.exception("Training %s failed", name)
                self._event("failure", f"{type(exc).__name__}: {str(exc)[:180]}")

        self.job = threading.Thread(target=run, daemon=True)
        self.job.start()

    def _event(self, name, value):
        if not self.closed.is_set():
            try:
                self.events.put_nowait((name, value))
            except queue.Full:
                LOG.error("Training event queue is full; frontend will detach")
                self.close()

    def action(self, action):
        if self.job and self.job.is_alive():
            self.output("\nPlease wait for the current operation. F10 can detach.\n")
            return
        if action == TRAIN_HUB:
            self._detach()
            self.hub()
        elif action == TRAIN_CHECK:
            if not self.manager.snapshot(self.identity).get("attempt"):
                self.hub("No active exercise to check.")
                return
            # Stop accepting terminal mutations while assessment runs.
            self._detach()
            self.output("Checking actual exercise state...\n")
            self._job(lambda: self.manager.check(self.identity), "checked")
        elif action == TRAIN_RESET:
            self._launch(self.manager.reset)
        elif action == TRAIN_ABANDON:
            self._detach()
            self._job(lambda: self.manager.abandon(self.identity), "abandoned")
        else:
            raise ProtocolError("unknown training action")

    def poll(self):
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if self.closed.is_set():
                continue
            if event != "shell_exit":
                self.job = None
            if event == "shell_exit":
                if value is self.shell_token:
                    self._detach()
                    self.hub("Shell closed. Exercise state remains available; choose R to resume.")
            elif event == "checked":
                status = value["status"]
                feedback = str(value.get("feedback", ""))
                if status == "passed":
                    text = "Passed. " + (f"Skill reward earned. +{value['reward_points']} skill points. " if value.get("rewarded") else "Practice completed; no reward. ")
                elif status == "incomplete":
                    text = "Incomplete. The next selection offers another challenge. "
                else:
                    text = "Environment/validator error. Attempt preserved; R resumes, F7 resets. "
                self.hub(text + feedback)
                self.rewards()
            elif event == "abandoned":
                self.hub("Exercise abandoned. Earned progress is retained.")
            elif event == "failure":
                self._detach()
                self.hub("Exercise unavailable; attempt preserved for reset/resume. " + value)
        if time.monotonic() - self.last_reward > 3:
            self.rewards()

    def rewards(self):
        self.last_reward = time.monotonic()
        for skill, level, points, receipt in self.manager.pending_rewards(self.identity, self.claimed):
            self.send(TRAIN_REWARD, encode_training_reward(skill, level, points, bytes.fromhex(receipt)))

    def acknowledge(self, skill, level, points, receipt, accepted):
        self.manager.acknowledge(self.identity, skill, level, points, receipt, accepted)
        if accepted:
            self.claimed[f"{skill}:{level}"] = {"receipt": receipt, "points": points}
        else:
            self.output("\nGame could not accept a reward; it remains available for reconciliation.\n")

    def resize(self, rows, cols):
        self.rows, self.cols = rows, cols
        with self.lock:
            if self.shell:
                self.shell.resize(rows, cols)

    def context(self, values):
        identity, skills = training_context(values)
        if identity != self.identity:
            raise ProtocolError("training profile cannot change inside a session")
        self._observe(identity, self.claimed, values)
        self.values, self.skills = dict(values), skills
        if self.mode == "hub" and not (self.job and self.job.is_alive()):
            self.hub(self.notice)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self._detach()
        if hasattr(self, "identity"):
            self.manager.detach_frontend(self.identity)
