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
import struct
import threading
import time
import textwrap

from protocol import (
    ERROR, OUTPUT, READY, TRAIN_REWARD, TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET, TRAIN_ABANDON,
    ProtocolError, encode_training_reward, training_context,
)

LOG = logging.getLogger("nixboy.training")
TRACKS = {3: "unarmed", 12: "science"}
STATE_VERSION = 1
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
        self.attached = set()
        self.closed = False
        self.reconciled = False
        self.reconcile_lock = threading.Lock()
        self.data = {"version": STATE_VERSION, "profiles": {}}
        if self.path.exists():
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("training state exceeds size limit")
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate_state()
        for profile in self.data["profiles"].values():
            attempt = profile.get("attempt")
            if attempt:
                attempt["status"] = "needs_reset"
        self._save()

    def _validate_state(self):
        if not isinstance(self.data, dict) or self.data.get("version") != STATE_VERSION \
                or not isinstance(self.data.get("profiles"), dict):
            raise ValueError("unsupported training state; preserve it for recovery")
        for key, profile in self.data["profiles"].items():
            if len(key) != 32 or any(ch not in "0123456789abcdef" for ch in key) \
                    or not isinstance(profile, dict):
                raise ValueError("invalid training profile state")
            for skill in TRACKS:
                earned = profile.get("earned", {}).get(str(skill), 0)
                if type(earned) is not int or not 0 <= earned <= 2:
                    raise ValueError("invalid earned level")
                for level in range(1, earned + 1):
                    receipt = profile.get("receipts", {}).get(f"{skill}:{level}", "")
                    if len(receipt) != 32 or any(ch not in "0123456789abcdef" for ch in receipt) \
                            or not any(bytes.fromhex(receipt)):
                        raise ValueError("missing completion receipt")
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
            "earned": {str(skill): 0 for skill in TRACKS}, "receipts": {},
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

    def next_level(self, identity, skill, completed):
        with self.lock:
            return max(self._profile(identity)["earned"][str(skill)], completed.get(skill, 0)) + 1

    def attach_frontend(self, identity):
        with self.lock:
            self.attached.add(identity)
            self._profile(identity)["updated"] = time.time()

    def detach_frontend(self, identity):
        with self.lock:
            self.attached.discard(identity)
            self._profile(identity)["updated"] = time.time()
            self._save()

    def observe(self, identity, completed, values):
        """Recover exact receipts from a game save, never invent completion evidence."""
        with self.lock:
            profile = self._profile(identity)
            receipts = dict(profile["receipts"])
            earned = dict(profile["earned"])
            recovered = list(profile.get("recovered_from_game", []))
            for skill, name in TRACKS.items():
                for level in range(1, completed[skill] + 1):
                    key = f"{skill}:{level}"
                    existing = receipts.get(key)
                    words = [values.get(f"training.receipt.{name}.{level}.{index}") for index in range(4)]
                    if all(type(word) is int and -(2**31) <= word < 2**31 for word in words):
                        receipt = b"".join(struct.pack("!I", word & 0xffffffff) for word in words)
                        if not any(receipt) or existing and existing != receipt.hex():
                            raise ProtocolError("saved reward receipt conflicts with training state")
                        if not existing:
                            receipts[key] = receipt.hex()
                            recovered.append(key)
                    elif not existing:
                        raise ProtocolError("completed game levels lack recovery receipts; restore training state")
                    earned[str(skill)] = max(earned[str(skill)], level)
            profile["receipts"], profile["earned"] = receipts, earned
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
        if skill not in TRACKS or level not in (1, 2) or mode not in ("assessment", "practice"):
            raise ValueError("Unsupported training selection")
        with self.lock:
            if self.closed:
                raise RuntimeError("Training service is stopping")
            profile = self._profile(identity)
            if profile.get("attempt"):
                raise ValueError("Resume or abandon the active exercise first")
            challenge = self._pool(profile, skill, level)[0]
            spec = self.catalog.get(challenge)
            seed = secrets.randbits(63)
            attempt = {"id": secrets.token_hex(16), "skill": skill, "level": level,
                       "challenge": challenge, "seed": seed, "generation": 0,
                       "challenge_version": getattr(spec, "version", 1),
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
                    self.state_dir, params=parameters["params"])
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
                receipt = None
                if result["status"] == "passed" and attempt["mode"] == "assessment":
                    skill, level = attempt["skill"], attempt["level"]
                    key = f"{skill}:{level}"
                    receipt = profile["receipts"].setdefault(key, secrets.token_hex(16))
                    profile["earned"][str(skill)] = max(profile["earned"][str(skill)], level)
                    # Preserve evidence even after the disposable environment is removed.
                    profile.setdefault("passes", {})[key] = {
                        "attempt": attempt["id"], "challenge": attempt["challenge"],
                        "challenge_version": attempt.get("challenge_version", 1),
                        "seed": attempt["seed"], "generation": attempt["generation"],
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

    def pending_rewards(self, identity, completed):
        with self.lock:
            profile = self._profile(identity)
            result = []
            for skill in TRACKS:
                for level in range(completed.get(skill, 0) + 1, profile["earned"][str(skill)] + 1):
                    receipt = profile["receipts"][f"{skill}:{level}"]
                    result.append((skill, level, receipt))
            return result

    def acknowledge(self, identity, skill, level, receipt, accepted):
        with self.lock:
            expected = self._profile(identity)["receipts"].get(f"{skill}:{level}")
            if expected != receipt:
                raise ProtocolError("acknowledgment does not match an earned receipt")
            if accepted:
                self._profile(identity).setdefault("acknowledged", {})[f"{skill}:{level}"] = receipt
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
        self.pages = []

    def start(self, rows, cols, context):
        self.rows, self.cols = rows, cols
        self.values = dict(context)
        self.identity, self.completed = training_context(context)
        self._observe(self.identity, self.completed, context)
        self.manager.attach_frontend(self.identity)
        self.send(READY)
        self.hub()
        self.rewards()

    def _observe(self, identity, completed, values):
        try:
            self.manager.observe(identity, completed, values)
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
        self.notice = notice
        self.mode, self.selected = "hub", None
        self.line.clear()
        state = self.manager.snapshot(self.identity)
        lines = ["NIX-BOY LEARNING HUB", "Knowledge powers your character.\n"]
        skills = sorted((key[6:], value) for key, value in self.values.items() if key.startswith("skill."))
        columns = 2 if self.cols >= 60 else 1
        for index in range(0, len(skills), columns):
            lines.append("  ".join(f"{name[:21]:21} {value:3}" for name, value in skills[index:index+columns]))
        lines.append("")
        for number, skill in enumerate(TRACKS, 1):
            earned = max(state["earned"][str(skill)], self.completed[skill])
            lines.append(f"[{number}] {TRACKS[skill].title()} training - completed {earned}/2")
        if state.get("attempt"):
            attempt = state["attempt"]
            lines.append(f"[R] Resume {TRACKS[attempt['skill']]} level {attempt['level']} ({attempt['mode']})")
        lines.extend(["F5 Check | F6 Hub | F7 Reset | F8 Abandon", "F10 Close (keeps an active exercise for later)"])
        if notice:
            lines.append(str(notice))
        lines.append("Choose a number or R, then Enter: ")
        self.output("\n".join(lines), clear=True)

    def track_menu(self, skill):
        state = self.manager.snapshot(self.identity)
        if state.get("attempt"):
            self.hub("An exercise is active. Resume it or use F8 to abandon it first.")
            return
        level = self.manager.next_level(self.identity, skill, self.completed)
        self.selected = (skill, min(level, 2))
        self.mode = "track"
        spec = self.manager.candidate(self.identity, skill, min(level, 2))
        finished = level > 2
        self.output(f"{TRACKS[skill].title()} - level {min(level, 2)}\n{spec.title}\n\n"
                    "[L] Lesson and guided instruction\n[P] Practice (no character reward)\n"
                    + ("Core track complete. Practice remains available.\n" if finished else
                       "[A] Assessment (earn +5 skill on success)\n")
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
        if self.mode == "lesson":
            if choice in ("p", "a", "h"):
                self.mode = "track"
                self._choice(choice)
            elif choice in ("n", "next") and self.pages:
                self._lesson_page()
            else:
                self.output("\nChoose N for the next page, P, A, or H.\n")
            return
        if self.mode == "hub":
            if choice in ("1", "2"):
                self.track_menu((3, 12)[int(choice) - 1])
            elif choice == "r":
                self._launch(self.manager.prepare)
            else:
                self.hub("Choose 1 or 2, or R to resume an active exercise.")
        elif self.mode == "track":
            skill, level = self.selected
            if choice == "h":
                self.hub()
            elif choice == "l":
                spec = self.manager.candidate(self.identity, skill, level)
                parameters = self.manager.catalog.instantiate(spec.id, 0)
                # Lessons contain ordinary Linux syntax such as awk '{print
                # $2}'. Replace only declared tokens; braces are otherwise text.
                lesson = spec.lesson
                for key, value in parameters.items():
                    lesson = lesson.replace("{" + key + "}", str(value))
                lines = []
                for paragraph in lesson.splitlines():
                    lines.extend(textwrap.wrap(paragraph, max(20, self.cols - 2)) or [""])
                size = max(1, self.rows - 6)
                self.pages = ["\n".join(lines[index:index + size]) for index in range(0, len(lines), size)]
                self.mode = "lesson"
                self._lesson_page()
            elif choice in ("p", "a"):
                if choice == "a" and self.manager.next_level(self.identity, skill, self.completed) > 2:
                    self.output("\nThis core track is complete. Choose P for practice.\n")
                    return
                self.manager.select(self.identity, skill, level,
                                    "practice" if choice == "p" else "assessment")
                self._launch(self.manager.prepare)
            else:
                self.output("\nChoose L, P, A, or H, then Enter: ")

    def _lesson_page(self):
        page = self.pages.pop(0) if self.pages else "End of lesson."
        footer = "[N] Next page | " if self.pages else ""
        self.output("LESSON\n\n" + page + "\n\n" + footer
                    + "[P] Practice | [A] Assessment | [H] Hub\nChoose a letter, then Enter: ", clear=True)

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
            self.output(f"{spec.title}\n{visible}\nFull task: cat ~/CHALLENGE.txt\n\nF5 Check | F6 Hub | F7 Reset | F8 Abandon\n")
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
                    text = "Passed. " + ("Skill reward earned. " if value.get("rewarded") else "Practice completed; no reward. ")
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
        for skill, level, receipt in self.manager.pending_rewards(self.identity, self.completed):
            self.send(TRAIN_REWARD, encode_training_reward(skill, level, bytes.fromhex(receipt)))

    def acknowledge(self, skill, level, receipt, accepted):
        self.manager.acknowledge(self.identity, skill, level, receipt, accepted)
        if accepted:
            self.completed[skill] = max(self.completed[skill], level)
        else:
            self.output("\nGame could not accept a reward; it remains available for reconciliation.\n")

    def resize(self, rows, cols):
        self.rows, self.cols = rows, cols
        with self.lock:
            if self.shell:
                self.shell.resize(rows, cols)

    def context(self, values):
        identity, completed = training_context(values)
        if identity != self.identity:
            raise ProtocolError("training profile cannot change inside a session")
        self._observe(identity, completed, values)
        self.values, self.completed = dict(values), completed
        if self.mode == "hub" and not (self.job and self.job.is_alive()):
            self.hub(self.notice)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self._detach()
        if hasattr(self, "identity"):
            self.manager.detach_frontend(self.identity)
