"""Current-run assignments and a shell-free terminal menu.

The character owns progression. This cache holds active work, alternatives,
a current project cursor and one completion delivery, never achievement history.
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
import inspect

from protocol import (
    ERROR, OUTPUT, READY, TRAIN_REWARD, TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET, TRAIN_ABANDON,
    MAX_CLAIMS, SKILL_NAMES, ProtocolError, encode_training_reward, training_context, validate_claims,
    TRAIN_PERK_REWARD, encode_training_perk_reward, validate_perk_claims,
)
from challenge_content import QEMU_KIND, GUEST_KINDS, IDENTIFIER
from course_progress import CourseProgress
from perk_rewards import PerkRewards

LOG = logging.getLogger("nixboy.training")
TRACKS = dict(enumerate(SKILL_NAMES))
STATE_VERSION = 1
STATE_SCOPE = "nixboy-runtime"
IDLE_SECONDS = 24 * 60 * 60


class OperationCancelled(RuntimeError):
    """A facility operation ended without producing an assessment result."""


def default_state_dir():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "NixBoy/active-assessments"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "nixboy/active-assessments"


class AttemptManager(CourseProgress, PerkRewards):
    """One durable attempt per profile; environments outlive frontend sockets."""

    def __init__(self, state_dir, client_factory, catalog=None, environment_factory=None,
                 cleanup=None):
        if catalog is None:
            from challenge_content import Catalog
            catalog = Catalog()
        if environment_factory is None:
            from challenge_env import Environment, cleanup_environments
            environment_factory = Environment
            cleanup = cleanup_environments
        self.catalog = catalog
        self.environment_factory = environment_factory
        self.client_factory = client_factory
        self.cleanup = cleanup
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.state_dir / "runtime.json"
        self.lock = threading.RLock()
        self.operations = {}
        self.cancel_events = {}
        self.environments = {}
        self.contexts = {}
        self.game_claims = {}
        self.game_perk_claims = {}
        self.attached = set()
        self.closed = False
        self.reconciled = False
        self.reconcile_lock = threading.Lock()
        self.data = {"scope": STATE_SCOPE, "version": STATE_VERSION, "profiles": {}}
        if self.path.exists():
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("training state exceeds size limit")
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate_state()
        for profile in self.data["profiles"].values():
            attempt = profile.get("attempt")
            if attempt:
                self._provider_stopped(attempt)
        self._save()

    def _is_guest(self, attempt):
        if not attempt:
            return False
        if "environment_kind" in attempt:
            return attempt["environment_kind"] in GUEST_KINDS
        try:
            return self.catalog.get(attempt["challenge"]).kind in GUEST_KINDS
        except (KeyError, ValueError):
            return False

    def _provider_stopped(self, attempt):
        if attempt.get("environment_kind") == "theory":
            return  # No execution provider is involved in a questionnaire.
        if self._is_guest(attempt) or attempt.get("status") in ("active", "checking", "provisioning", "provider_lost"):
            attempt["status"] = "provider_lost"
            attempt["facility_error"] = "Execution provider interrupted. No learner failure or reward was recorded. Use F7 to reissue the canonical attempt."
            if attempt.get("course_gate") and attempt.get("conceptual"):
                attempt["facility_error"] = "Execution provider interrupted; no learner failure. Questions were committed: use T for a fresh equivalent reassessment."
        else:
            attempt["status"] = "needs_reset"

    def cancel(self, identity):
        """Signal the current bounded operation without taking its operation lock."""
        with self.lock:
            event = self.cancel_events.get(identity)
            if event:
                event.set()

    def attachment(self, identity, rows=None, cols=None, cancel=None):
        with self.lock:
            environment = self.environments.get(identity)
        if environment and rows is not None and hasattr(environment, "set_console_size"):
            environment.set_console_size(rows, cols, cancel=cancel)
        return environment.attachment() if environment and hasattr(environment, "attachment") else None

    def _validate_state(self):
        if not isinstance(self.data, dict) or self.data.get("scope") != STATE_SCOPE \
                or self.data.get("version") != STATE_VERSION or not isinstance(self.data.get("profiles"), dict) \
                or len(self.data["profiles"]) > 1:
            raise ValueError("Expected minimal runtime state; old progression snapshots are not imported")
        for identity, profile in self.data["profiles"].items():
            required = {"queues", "assessment_queues", "attempt", "project", "last_assessment", "pending"}
            if not isinstance(identity, str) or len(identity) != 32 or any(ch not in "0123456789abcdef" for ch in identity) \
                    or not isinstance(profile, dict) or not required <= set(profile) or set(profile) - required - {"updated"}:
                raise ValueError("invalid runtime attempt state")
            if any(not isinstance(profile.get(key, {}), dict) for key in ("queues", "assessment_queues")):
                raise ValueError("invalid runtime alternative queue")
            if profile.get("attempt") is not None and not isinstance(profile["attempt"], dict):
                raise ValueError("invalid active assessment")
            project = profile.get("project")
            if project is not None and (not isinstance(project, dict) or set(project) != {"id", "next_stage"}
                    or not isinstance(project["id"], str) or not IDENTIFIER.fullmatch(project["id"])
                    or type(project["next_stage"]) is not int or not 1 <= project["next_stage"] <= 9):
                raise ValueError("invalid current project cursor")
            for queue in profile.get("assessment_queues", {}).values():
                if not isinstance(queue, dict) or set(queue) != {"authored", "pending", "exposed"}:
                    raise ValueError("invalid assessment alternative accounting")
                for key in ("authored", "pending", "exposed"):
                    values = queue[key]
                    if not isinstance(values, list) or len(values) > 32 or any(
                            not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in values) \
                            or len(set(values)) != len(values):
                        raise ValueError("invalid assessment alternative queue")
                if not set(queue["pending"]) <= set(queue["authored"]) or not set(queue["exposed"]) <= set(queue["authored"]):
                    raise ValueError("assessment queue refers to an unauthored alternative")
            pending = profile.get("pending")
            if pending is not None:
                self._validate_pending(identity, pending)

    def _validate_pending(self, identity, pending):
        if not isinstance(pending, dict) or pending.get("kind") not in ("skill", "perk") \
                or type(pending.get("accepted")) is not bool:
            raise ValueError("invalid current completion delivery")
        if pending["kind"] == "skill":
            validate_claims({f"{pending['skill']}:{pending['level']}":
                {"receipt": pending["receipt"], "points": pending["points"]}})
        else:
            validate_perk_claims({f"{pending['perk']}:{pending['rank']}": pending["receipt"]})
            if pending.get("profile") != identity:
                raise ValueError("current perk delivery belongs to another runtime")

    def _save(self):
        """Atomically replace the current runtime cache, not a game-save snapshot."""
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
        try:
            return self.data["profiles"][identity]
        except KeyError as exc:
            raise ProtocolError("Runtime changed; stale attempt or delivery discarded") from exc

    def _new_receipt(self, identity):
        used = {item["receipt"] for item in self.game_claims.get(identity, {}).values()}
        used.update(self.game_perk_claims.get(identity, {}).values())
        pending = self._profile(identity).get("pending")
        if pending:
            used.add(pending["receipt"])
        receipt = secrets.token_hex(16)
        while receipt in used:
            receipt = secrets.token_hex(16)
        return receipt

    def _pending_guard(self, profile):
        if profile.get("pending") and not profile["pending"]["accepted"]:
            raise ValueError("The current completion is awaiting game acknowledgment; reopen nix-boy to finish delivery")

    def snapshot(self, identity):
        with self.lock:
            profile = self._profile(identity)
            profile["updated"] = time.time()
            return json.loads(json.dumps(profile))

    def _operation(self, identity):
        with self.lock:
            return self.operations.setdefault(identity, threading.RLock())

    def current_level(self, identity, skill):
        with self.lock:
            value = self.contexts.get(identity, {}).get("skill." + TRACKS[skill])
            return self.catalog.level_for_value(skill, value)

    def _eligible(self, identity, skill, level, mode):
        if self.current_level(identity, skill) != level:
            raise ValueError("The current skill value has no matching exercise at this level; abandon and reselect")
        if mode == "assessment" and f"{skill}:{level}" in self.game_claims.get(identity, {}):
            raise ValueError("This skill band's reward is already claimed; practice remains available")
        if mode == "assessment" and len(self.game_claims.get(identity, {})) >= MAX_CLAIMS:
            raise ValueError("This character has reached the supported training receipt limit")

    def attach_frontend(self, identity):
        with self.lock:
            self.attached.add(identity)
            self._profile(identity)["updated"] = time.time()

    def detach_frontend(self, identity):
        with self.lock:
            self.attached.discard(identity)
            if identity in self.data["profiles"]:
                self._profile(identity)["updated"] = time.time()
                self._save()

    def observe(self, identity, claimed, values):
        """Observe current character state; never import externally earned history."""
        validate_claims(claimed)
        with self.lock:
            previous = [key for key in self.data["profiles"] if key != identity]
        # The game renews its existing runtime identity on load/new game. This
        # provider serves one game; replacing a run cancels only its owned work.
        for old in previous:
            self.cancel(old)
            with self._operation(old):
                self._remove_environment(old)
                with self.lock:
                    self.data["profiles"].pop(old, None)
                    self.contexts.pop(old, None)
                    self.game_claims.pop(old, None)
                    self.game_perk_claims.pop(old, None)
        with self.lock:
            if identity not in self.data["profiles"]:
                self.data["profiles"][identity] = {"queues": {}, "assessment_queues": {},
                    "attempt": None, "project": None, "last_assessment": None, "pending": None}
            profile = self._profile(identity)
            if {item["receipt"] for item in claimed.values()} & set(self.game_perk_claims.get(identity, {}).values()):
                raise ProtocolError("receipt reused across character reward types")
            self.game_claims[identity] = dict(claimed)
            self.contexts[identity] = dict(values)
            pending = profile.get("pending")
            if pending and pending["kind"] == "skill":
                key = f"{pending['skill']}:{pending['level']}"
                if key in claimed:
                    if claimed[key] != {"receipt": pending["receipt"], "points": pending["points"]}:
                        raise ProtocolError("current completion conflicts with character reward")
                    pending["accepted"] = True
            profile["updated"] = time.time()
            self._save()

    def _pool(self, profile, skill, level):
        key = f"{skill}:{level}"
        authored = self.catalog.pool(skill, level)
        return self._ordered_pool(profile, key, authored)

    def _ordered_pool(self, profile, key, authored):
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
            self._pending_guard(profile)
            self._eligible(identity, skill, level, mode)
            challenge = self._pool(profile, skill, level)[0]
            spec = self.catalog.get(challenge)
            seed = secrets.randbits(63)
            attempt = {"id": secrets.token_hex(16), "skill": skill, "level": level,
                       "challenge": challenge, "seed": seed, "generation": 0,
                       "environment_kind": spec.kind,
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

    def prepare(self, identity, cancel=None, progress=None):
        with self.lock:
            attempt = self._profile(identity).get("attempt")
            if attempt and attempt.get("environment_kind") == "theory":
                self._check_content(attempt)
                return None  # Entering pure theory never even reconciles Docker.
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
                if attempt["status"] == "provider_lost":
                    raise RuntimeError(attempt.get("facility_error", "Guest provider interrupted; use F7 to reissue."))
                if identity in self.environments:
                    environment = self.environments[identity]
                    if self._is_guest(attempt):
                        observed = environment.status()
                        attempt["machine_state"] = observed.get("state", "unknown")
                        if observed.get("state") == "failure":
                            self._provider_stopped(attempt)
                            self._save()
                            raise RuntimeError(attempt["facility_error"] + " " + str(observed.get("feedback", "")))
                    elif hasattr(environment, "health"):
                        observed = environment.health()
                        if observed.get("status") != "ready":
                            self._provider_stopped(attempt)
                            self._save()
                            raise RuntimeError(attempt["facility_error"] + " " + str(observed.get("feedback", "")))
                    return environment.container_id
                if attempt["status"] == "needs_reset":
                    attempt["generation"] += 1
                attempt["status"] = "provisioning"
                self._save()
                spec = self.catalog.get(attempt["challenge"])
                if getattr(spec, "version", 1) != attempt.get("challenge_version", 1):
                    raise ValueError("Exercise content changed; abandon this attempt and select a new one")
                parameters = dict(attempt)
                cancel = cancel if cancel is not None else threading.Event()
                self.cancel_events[identity] = cancel
            client = self.client_factory()
            environment = None
            try:
                environment = self.environment_factory(
                    client, spec, parameters["id"], parameters["seed"], parameters["generation"],
                    self.state_dir, params=parameters["params"], content_root=self.catalog.root,
                    training_label=self._training_label(parameters))
                if cancel.is_set():
                    raise OperationCancelled("Preparation cancelled; no learner result recorded.")
                supports_cancel = "cancel" in inspect.signature(environment.start).parameters
                container_id = (environment.start(cancel=cancel, progress=progress)
                                if supports_cancel else environment.start())
                if parameters.get("preparation_identity") and (
                        getattr(environment, "preparation_identity", None) != parameters["preparation_identity"]):
                    raise RuntimeError("Canonical stage preparation identity mismatch")
                with self.lock:
                    if self.closed:
                        raise RuntimeError("Training service is stopping")
                    if cancel.is_set():
                        raise OperationCancelled("Preparation cancelled; provisional resources removed.")
                    self.environments[identity] = environment
                    attempt["status"] = "active"
                    attempt.pop("facility_error", None)
                    if self._is_guest(attempt):
                        attempt["machine_state"] = environment.status().get("state", "recovery")
                    attempt["updated"] = time.time()
                    self._save()
                return container_id
            except Exception as exc:
                if environment:
                    try:
                        environment.close()
                    except Exception:
                        LOG.exception("Training partial startup cleanup failed")
                else:
                    client.close()
                with self.lock:
                    self.environments.pop(identity, None)
                    if not cancel.is_set():
                        self._provider_stopped(attempt)
                        attempt["facility_error"] = "Facility startup failed; use F7 to reissue. " + str(exc)[:500]
                    else:
                        attempt["status"] = "needs_reset"
                    self._save()
                if cancel.is_set():
                    raise OperationCancelled("Preparation cancelled; owned provisional resources removed.") from exc
                raise
            finally:
                with self.lock:
                    if self.cancel_events.get(identity) is cancel:
                        self.cancel_events.pop(identity, None)

    def _training_label(self, attempt):
        if attempt.get("course_gate"):
            gate = self._course().get(attempt["course_gate"])
            reward = "test-only owned Toughness rank 1 -> 2" if gate.reward else "no game reward"
            return f"Educational integration fixture / {attempt['course_gate']} ({attempt['alternative']}); {reward}"
        track = self.catalog.tracks[attempt["skill"]]
        band = track["levels"][attempt["level"]]
        reward = attempt["reward_points"]
        return f"{track['knowledge_title']} / {band['title']} - level {attempt['level']} ({track['title']}); assessment reward +{reward}"

    def _check_content(self, attempt):
        if attempt.get("course_gate"):
            if attempt.get("course_digest") != self._course().digest(attempt["course_gate"], attempt["alternative"]):
                raise ValueError("Educational content changed; abandon and reselect. Earned completion is preserved")
            return
        course = getattr(self.catalog, "course", None)
        if course and attempt.get("challenge") in course.challenge_ids:
            raise ValueError("This challenge is course-exclusive; abandon and select its educational gate")
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

    def check(self, identity, cancel=None, progress=None):
        with self.lock:
            attempt = self._profile(identity).get("attempt")
            is_course = bool(attempt and attempt.get("course_gate"))
        if is_course:
            return self._course_check(identity, cancel=cancel, progress=progress)
        with self._operation(identity):
            with self.lock:
                profile = self._profile(identity)
                attempt = profile.get("attempt")
                environment = self.environments.get(identity)
                if not attempt or not environment:
                    return {"status": "error", "feedback": "Resume the exercise before checking."}
                if attempt["status"] == "provider_lost":
                    return {"status": "error", "feedback": attempt.get("facility_error", "Guest provider interrupted; use F7 to reissue.")}
                try:
                    self._check_content(attempt)
                except ValueError as exc:
                    return {"status": "error", "feedback": str(exc)}
                if self.current_level(identity, attempt["skill"]) != attempt["level"]:
                    return {"status": "error", "feedback": "Your skill value changed bands. Files are preserved; abandon and select your current band."}
                attempt["status"] = "checking"
                cancel = cancel if cancel is not None else threading.Event()
                self.cancel_events[identity] = cancel
                self._save()
            try:
                result = (environment.check(cancel=cancel, progress=progress)
                          if self._is_guest(attempt) else environment.check())
                if not isinstance(result, dict) or result.get("status") not in ("passed", "incomplete", "error"):
                    raise ValueError("Validator returned an invalid result")
            except Exception:
                LOG.exception("Training validator failed for %s", attempt["id"])
                result = {"status": "error", "feedback": "Validator/environment error; your attempt is preserved."}
            finally:
                with self.lock:
                    if self.cancel_events.get(identity) is cancel:
                        self.cancel_events.pop(identity, None)
            evidence = None
            if result["status"] == "passed" and "evidence" in result:
                try:
                    encoded = json.dumps(result["evidence"], sort_keys=True, allow_nan=False)
                    if len(encoded.encode("utf-8")) > 65536:
                        raise ValueError("evidence exceeds 64 KiB")
                    evidence = json.loads(encoded)
                except (TypeError, ValueError):
                    result = {"status": "error", "feedback": "Trusted validation evidence is invalid or exceeds its persistence bound; attempt preserved."}
            with self.lock:
                if self.closed:
                    self._provider_stopped(attempt)
                    self._save()
                    self._remove_environment(identity)
                    return {"status": "error", "feedback": "Training service is stopping; attempt preserved."}
                attempt["status"] = "active"
                attempt["updated"] = time.time()
                if cancel.is_set():
                    result = {"status": "error", "feedback": "Check cancelled. No reward or learner failure was recorded; attempt preserved."}
                if result["status"] == "error":
                    if result.get("facility_failure"):
                        self._provider_stopped(attempt)
                    if self._is_guest(attempt):
                        attempt["last_facility_feedback"] = str(result.get("feedback", ""))[:1000]
                        if result.get("facility_failure") or result.get("state") == "failure":
                            self._provider_stopped(attempt)
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
                before_completion = json.loads(json.dumps(profile))
                receipt = None
                if result["status"] == "passed" and attempt["mode"] == "assessment":
                    skill, level = attempt["skill"], attempt["level"]
                    key = f"{skill}:{level}"
                    if key in self.game_claims.get(identity, {}) or len(self.game_claims.get(identity, {})) >= MAX_CLAIMS:
                        return {"status": "error", "feedback": "This character already has the band's reward."}
                    self._pending_guard(profile)
                    receipt = self._new_receipt(identity)
                    profile["pending"] = {"kind": "skill", "skill": skill, "level": level,
                        "points": attempt["reward_points"], "receipt": receipt, "accepted": False}
                self._rotate(profile, attempt)
                profile["attempt"] = None
                profile["updated"] = time.time()
                try:
                    self._save()  # Current completion durable before delivery.
                except Exception:
                    profile.clear()
                    profile.update(before_completion)
                    raise
            try:
                self._remove_environment(identity)
            except Exception:
                LOG.exception("Completed training cleanup failed; restart reconciliation will retry")
            result = dict(result)
            result["rewarded"] = receipt is not None
            if receipt is not None:
                result["reward_points"] = attempt["reward_points"]
            return result

    def reset(self, identity, cancel=None, progress=None):
        with self._operation(identity):
            with self.lock:
                attempt = self._profile(identity).get("attempt")
                if not attempt:
                    raise ValueError("No active exercise to reset")
                if attempt.get("course_gate") and attempt.get("conceptual"):
                    raise ValueError("Questions were committed; use T for explicit reassessment with a fresh equivalent alternative")
            self._remove_environment(identity)
            with self.lock:
                attempt["status"] = "needs_reset"
                attempt.pop("facility_error", None)
                if attempt.get("course_gate"):
                    attempt.update(answers={}, conceptual=None, practical=None, checkpoints=[])
                    if attempt.get("environment_kind") == "theory":
                        attempt["generation"] += 1
                self._save()
        return self.prepare(identity, cancel=cancel, progress=progress)

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
            pending = self._profile(identity).get("pending")
            if not pending or pending["kind"] != "skill" or pending["accepted"]:
                return []
            key = f"{pending['skill']}:{pending['level']}"
            if key in claimed:
                if claimed[key] != {"receipt": pending["receipt"], "points": pending["points"]}:
                    raise ProtocolError("current completion conflicts with character reward")
                self.game_claims.setdefault(identity, {})[key] = dict(claimed[key])
                pending["accepted"] = True
                self._save()
                return []
            return [(pending["skill"], pending["level"], pending["points"], pending["receipt"])]

    def acknowledge(self, identity, skill, level, points, receipt, accepted):
        with self.lock:
            pending = self._profile(identity).get("pending")
            expected = {"kind": "skill", "skill": skill, "level": level,
                        "points": points, "receipt": receipt}
            if not pending or any(pending.get(key) != value for key, value in expected.items()):
                raise ProtocolError("acknowledgment does not match the current completion")
            if accepted:
                pending["accepted"] = True
                self.game_claims.setdefault(identity, {})[f"{skill}:{level}"] = {"receipt": receipt, "points": points}
                self._save()

    def expire(self):
        """Issued assignments have no silent idle deadline; explicit cleanup owns them."""
        return

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.cancel_events.values():
                event.set()
            # Provisioning/checking may hold an operation before an environment
            # has been registered. Wait for those workers before process exit.
            identities = list(set(self.environments) | set(self.operations))
            for profile in self.data["profiles"].values():
                if profile.get("attempt"):
                    self._provider_stopped(profile["attempt"])
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
        self.job_cancel = None
        self.cancel_next = None
        self.mode = "hub"
        self.line = bytearray()
        self.selected = None
        self.last_reward = 0
        self.hub_page = 0

    def start(self, rows, cols, context, progress, perk_progress=None):
        self.rows, self.cols = rows, cols
        self.values = dict(context)
        self.identity, self.skills = training_context(context)
        self.claimed = dict(validate_claims(progress))
        self.perk_claimed = dict(validate_perk_claims(perk_progress or {}))
        self._observe(self.identity, self.claimed, context)
        self.manager.observe_perks(self.identity, self.perk_claimed)
        self.manager.attach_frontend(self.identity)
        self.send(READY)
        self.hub()
        self.rewards()

    def _observe(self, identity, claimed, values):
        try:
            self.manager.observe(identity, claimed, values)
        except ProtocolError as exc:
            self.send(ERROR, ("Current character state rejected: " + str(exc)).encode("utf-8")[:4096])
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
        lines = ["nix-boy / Training hub", "Training connected - knowledge powers your character.", ""]
        width = max(20, self.cols - 1)
        for index in range(self.hub_page * size, min(len(tracks), (self.hub_page + 1) * size)):
            skill = tracks[index]
            track = self.manager.catalog.tracks[skill]
            value = self.skills[skill]
            level = self.manager.current_level(self.identity, skill)
            band = track["levels"].get(level)
            completed = sum(f"{skill}:{number}" in self.claimed for number in track["levels"])
            lines.append(f"[{index + 1}] {track['title']} {value}% | {track['knowledge_title']}"[:width])
            availability = (f"level {level} ({band['min_skill']}-{band['max_skill']}%): {band['title']} [+{band.get('reward_points', 5)}]" if band else
                            "no authored challenges at this value")
            lines.append(f"    {availability}; completed {completed}/{len(track['levels'])}"[:width])
        if pages > 1:
            lines.append(f"Page {self.hub_page + 1}/{pages} | [N] Next | [B] Back")
        if state.get("attempt"):
            attempt = state["attempt"]
            label = attempt.get("course_gate") or f"{SKILL_NAMES[attempt['skill']]} level {attempt['level']}"
            lines.append(f"[R] Resume {label} ({attempt['mode']})"[:width])
            if attempt.get("facility_error"):
                lines.extend(textwrap.wrap(attempt["facility_error"], width)[:2])
        if getattr(self.manager.catalog, "course", None):
            lines.append("[C] Assessments | [Q] Questions | [T] Explicit retry | X <checkpoint>")
        lines.extend(["F5 Check | F6 Hub | F7 Reset | F8 Abandon", "F10 Close (keeps an active exercise for later)"])
        if self.notice:
            lines.extend(textwrap.wrap(self.notice, width)[:3])
        lines.append("Choose a number or R, then Enter: ")
        self.output("\n".join(lines), clear=True)

    def course_menu(self, notice=""):
        self.mode = "course"
        self.line.clear()
        self.course_choices = self.manager.course_status(self.identity)
        lines = ["Assessments / explicit test mappings only", ""]
        for index, gate in enumerate(self.course_choices, 1):
            status = "stage passed" if gate["complete"] else "available" if gate["eligible"] else "not current stage / alternatives used"
            lines.extend(textwrap.wrap(f"[{index}] {gate['id']} ({gate['format']}, {status}) - {gate['title']}", max(20, self.cols - 2)))
        lines.extend(["", str(notice), "Choose a gate number, T to retry, or H for hub: "])
        self.output("\n".join(lines), clear=True)

    def question_menu(self, notice=""):
        state = self.manager.questions(self.identity)
        self.mode = "questions"
        self.line.clear()
        lines = ["Educational questions / whole-set commitment", ""]
        if self.manager.snapshot(self.identity)["attempt"].get("exposed_retry"):
            lines.append("Previously exposed alternative: remediation retry, not unseen retention evidence.")
        questions = state["questions"]
        page = min(getattr(self, "question_page", 0), max(0, len(questions) - 1))
        self.question_page = page
        if questions:
            question = questions[page]
            lines.extend(textwrap.wrap(f"{question['id']}: {question['prompt']}", max(20, self.cols - 2)))
            for index, value in enumerate(question.get("choices", []), 1):
                lines.extend(textwrap.wrap(f"  {index}. {value}", max(20, self.cols - 2)))
            lines.append("Draft: " + str(state["answers"].get(question["id"], "(none)")))
        if state["committed"]:
            feedback = self.manager.course_feedback(self.identity)
            lines.append("Committed / " + ("conceptual passed" if feedback["passed"] else "review needed"))
            lines.extend(textwrap.wrap(str(feedback.get("feedback", "")), max(20, self.cols - 2)))
            for item in feedback.get("items", []):
                lines.extend(textwrap.wrap(f"{item['id']}: {item.get('feedback', '')}", max(20, self.cols - 2)))
            lines.append("F5 final submission | T explicit reassessment | H hub")
        else:
            lines.extend([f"Question {page + 1}/{len(questions)}; drafts {len(state['answers'])}/{len(questions)}",
                          "Enter <question-id> <answer> | N next | B back", "COMMIT grades the complete set. H returns to hub."])
        if notice:
            lines.extend(textwrap.wrap(str(notice), max(20, self.cols - 2))[:3])
        self.output("\n".join(lines) + "\n> ", clear=True)

    def _resume(self):
        attempt = self.manager.snapshot(self.identity).get("attempt")
        if attempt and attempt.get("environment_kind") == "theory":
            self.question_menu()
        else:
            self._launch(self.manager.prepare)

    def _course_retry(self):
        self.manager.retry_course(self.identity)
        self.question_page = 0
        self._resume()

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
        finished = f"{skill}:{level}" in self.claimed
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
                value = self.line.decode("ascii")
                if self.mode != "questions":
                    value = value.strip()
                self.line.clear()
                self._choice(value)
            elif char in (8, 127):
                if self.line:
                    self.line.pop()
                    self.output("\b \b")
            elif 32 <= char <= 126 and len(self.line) < (512 if self.mode == "questions" else 64):
                self.line.append(char)
                self.output(chr(char))

    def _choice(self, choice):
        raw_choice, choice = choice, choice.strip().lower()
        if self.mode == "questions":
            try:
                if choice == "h":
                    self.hub()
                elif choice == "t":
                    self._course_retry()
                elif choice in ("n", "b"):
                    self.question_page = max(0, self.question_page + (1 if choice == "n" else -1))
                    self.question_menu()
                elif choice == "commit":
                    self.manager.commit_answers(self.identity)
                    self.question_menu("Committed; practical completion is separately required for mixed assessments.")
                else:
                    question, answer = raw_choice.split(" ", 1)
                    self.manager.draft_answer(self.identity, question, answer)
                    self.question_menu("Draft saved. No grading before COMMIT.")
            except ValueError as exc:
                self.output("\n" + str(exc) + "\n> ")
            return
        if self.mode == "course":
            try:
                if choice == "h":
                    self.hub()
                elif choice == "t":
                    self._course_retry()
                elif choice.isdigit() and 1 <= int(choice) <= len(self.course_choices):
                    gate = self.course_choices[int(choice) - 1]
                    attempt = self.manager.select_course(self.identity, gate["id"])
                    self.question_page = 0
                    if attempt.get("exposed_retry"):
                        self.output("All alternatives have been exposed; this is a fresh remediation retry, not unseen retention evidence.\n")
                    self._resume()
                else:
                    self.course_menu("Choose a listed gate or H.")
            except ValueError as exc:
                self.course_menu(str(exc))
            return
        if self.mode == "hub":
            if choice in ("c", "q", "t") or choice.startswith("x "):
                try:
                    if choice == "c":
                        self.course_menu()
                    elif choice == "q":
                        self.question_menu()
                    elif choice == "t":
                        self._course_retry()
                    else:
                        self._job(lambda: self.manager.checkpoint(self.identity, choice[2:].strip()), "checkpoint")
                except ValueError as exc:
                    self.hub(str(exc))
            elif choice.isdigit() and 1 <= int(choice) <= len(self.manager.catalog.tracks):
                self.track_menu(list(self.manager.catalog.tracks)[int(choice) - 1])
            elif choice in ("n", "b"):
                self.hub_page = max(0, self.hub_page + (1 if choice == "n" else -1))
                self.hub()
            elif choice == "r":
                try:
                    self._resume()
                except ValueError as exc:
                    self.hub(str(exc))
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
        self.output("Preparing exercise; F8 cancels/abandons, F7 restarts, F10 closes.\n", clear=True)

        def work():
            container_id = operation(self.identity, cancel=self.job_cancel, progress=self._progress)
            if self.closed.is_set():
                return None
            state = self.manager.snapshot(self.identity)
            attempt = state.get("attempt")
            if not attempt:
                return None
            if attempt.get("environment_kind") == "theory":
                return "questions"
            spec = self.manager.catalog.get(attempt["challenge"])
            instructions = spec.instructions.format(**attempt['params'])
            lines = textwrap.wrap(instructions, max(20, self.cols - 2))
            visible = "\n".join(lines[:max(1, self.rows - 8)])
            attachment = self.manager.attachment(self.identity, self.rows, self.cols, cancel=self.job_cancel)
            task_path = "/root/CHALLENGE.txt" if attachment else "~/CHALLENGE.txt"
            machine = ("Guest state: " + attempt.get("machine_state", "unknown") + ".\n") if attachment else ""
            self.output(f"{self.manager._training_label(attempt)}\n{spec.title}\n{visible}\nFull task: cat {task_path}\n{machine}\nF5 Check | F6 Hub | F7 Reset | F8 Abandon\n")
            token = object()
            options = {"attachment": attachment} if attachment else {}
            shell = self.shell_factory(container_id, self.send,
                                       lambda: self._event("shell_exit", token), **options)
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
        self.job_cancel = threading.Event()

        def run():
            try:
                self._event(name, work())
            except OperationCancelled as exc:
                self._event("cancelled", str(exc))
            except Exception as exc:
                LOG.exception("Training %s failed", name)
                self._event("failure", f"{type(exc).__name__}: {str(exc)[:180]}")

        self.job = threading.Thread(target=run, daemon=True)
        self.job.start()

    def _progress(self, message):
        self._event("progress", str(message)[:1000])

    def _event(self, name, value):
        if not self.closed.is_set():
            try:
                self.events.put_nowait((name, value))
            except queue.Full:
                LOG.error("Training event queue is full; frontend will detach")
                self.close()

    def action(self, action):
        if self.job and self.job.is_alive():
            if action in (TRAIN_ABANDON, TRAIN_RESET):
                self.cancel_next = action
                self.job_cancel.set()
                self.manager.cancel(self.identity)
                self.output("\nCancelling the current operation; owned provisional resources will be cleaned.\n")
            else:
                self.output("\nOperation in progress. F8 cancels/abandons; F7 cancels/resets; F10 closes.\n")
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
            self._job(lambda: self.manager.check(self.identity, cancel=self.job_cancel, progress=self._progress), "checked")
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
            if event == "progress":
                self.output("\n" + value + "\n")
                continue
            if event != "shell_exit":
                self.job = None
                if self.cancel_next is not None:
                    action, self.cancel_next = self.cancel_next, None
                    if action == TRAIN_RESET:
                        self._launch(self.manager.reset)
                    else:
                        self._detach()
                        self._job(lambda: self.manager.abandon(self.identity), "abandoned")
                    continue
            if event == "shell_exit":
                if value is self.shell_token:
                    self._detach()
                    self.hub("Shell closed. Exercise state remains available; choose R to resume.")
            elif event == "launch" and value == "questions":
                self.question_menu()
            elif event == "checked":
                status = value["status"]
                feedback = str(value.get("feedback", ""))
                if status == "passed":
                    text = "Passed. " + (f"Skill reward earned. +{value['reward_points']} skill points. " if value.get("rewarded") else "Passive perk reward earned. " if value.get("perk_rewarded") else "Assessment complete; no game reward. " if value.get("assessment") else "Practice completed; no reward. ")
                elif status == "pending":
                    text = "Intermediate progress only. "
                elif status == "incomplete":
                    text = "Incomplete. The next selection offers another challenge. "
                elif value.get("facility_state") == "recovery":
                    text = "Expected recovery; no learner result recorded. R returns to the guest console. "
                elif value.get("facility_state") in ("booting", "runner-ready"):
                    text = "Guest startup is still in progress; no learner result recorded. Wait, then R resumes. "
                elif value.get("facility_state") == "off":
                    text = "Guest powered off; no learner result recorded. F7 reissues the canonical attempt. "
                elif value.get("facility_failure"):
                    text = "Guest facility failure; no reward or learner failure recorded. F7 reissues the attempt. "
                else:
                    text = "Environment/validator error. Attempt preserved; R resumes, F7 resets. "
                self.hub(text + feedback)
                self.rewards()
            elif event == "checkpoint":
                self.hub("Checkpoint " + value["status"] + ": " + str(value.get("feedback", "")))
            elif event == "abandoned":
                self.hub("Exercise abandoned. Earned progress is retained.")
            elif event == "failure":
                self._detach()
                self.hub("Exercise unavailable; attempt preserved for reset/resume. " + value)
            elif event == "cancelled":
                self._detach()
                self.hub("Operation cancelled; no learner result recorded. " + value)
        if time.monotonic() - self.last_reward > 3:
            self.rewards()

    def rewards(self):
        self.last_reward = time.monotonic()
        for skill, level, points, receipt in self.manager.pending_rewards(self.identity, self.claimed):
            self.send(TRAIN_REWARD, encode_training_reward(skill, level, points, bytes.fromhex(receipt)))
        if self.values.get("training.version") == 5:
            for profile, perk, rank, receipt in self.manager.pending_perks(self.identity, self.perk_claimed):
                self.send(TRAIN_PERK_REWARD, encode_training_perk_reward(profile, perk, rank, bytes.fromhex(receipt)))

    def acknowledge_perk(self, profile, perk, rank, receipt, accepted):
        self.manager.acknowledge_perk(self.identity, profile, perk, rank, receipt, accepted)
        if accepted:
            self.perk_claimed[f"{perk}:{rank}"] = receipt
        else:
            self.output("\nPerk reward not applied (ownership/rank/profile guard). Receipt retained for reconciliation.\n")

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
        if values.get("training.version") != self.values.get("training.version"):
            raise ProtocolError("training protocol version cannot change inside a session; reopen")
        self._observe(identity, self.claimed, values)
        self.values, self.skills = dict(values), skills
        if self.mode == "hub" and not (self.job and self.job.is_alive()):
            self.hub(self.notice)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        if self.job_cancel and self.job and self.job.is_alive():
            self.job_cancel.set()
            self.manager.cancel(self.identity)
        self._detach()
        if hasattr(self, "identity"):
            self.manager.detach_frontend(self.identity)
