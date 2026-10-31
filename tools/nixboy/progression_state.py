"""Save-selected progression snapshots and non-rollback activation fences.

The mutable profile is the current timeline. Immutable snapshots bind a complete
profile to the game ledgers captured by one game save. Activation metadata lives
outside those snapshots so loading an earlier save cannot revive stale clients.
"""
import copy
import hashlib
import json
import os
import re
import secrets
import time

from education_records import validate_profile
from protocol import ProtocolError, validate_claims, validate_perk_claims

ZERO_SNAPSHOT = "0" * 64
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_RETIRED_ACTIVATIONS = 4096


def _hex(value, size, allow_zero=False):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % size, value) \
            or (not allow_zero and not any(ch != "0" for ch in value)):
        raise ProtocolError("invalid progression identity")
    return value


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class ProgressionState:
    def assert_activation(self, identity, activation):
        _hex(identity, 32)
        _hex(activation, 32)
        with self.lock:
            active = self.data.get("activations", {}).get(identity)
            if active is None or active.get("activation") != activation:
                raise ProtocolError("Progression timeline changed; stale session or delivery rejected. Reopen nix-boy.")

    def _check_saved_claims(self, identity, profile, claimed, perk_claimed):
        validate_claims(claimed)
        validate_perk_claims(perk_claimed)
        validate_profile(identity, profile)
        for key, record in claimed.items():
            if profile.get("receipts", {}).get(key) != record:
                raise ProtocolError("Game skill claims do not belong to the selected progression state")
        for key, receipt in perk_claimed.items():
            if profile.get("perk_receipts", {}).get(key, {}).get("receipt") != receipt:
                raise ProtocolError("Game perk claims do not belong to the selected progression state")
        if {record["receipt"] for record in claimed.values()} & set(perk_claimed.values()):
            raise ProtocolError("receipt reused across reward types")

    def _read_progression_snapshot(self, identity, snapshot, claimed, perk_claimed):
        path = self.state_dir / "progression-snapshots" / (snapshot + ".json")
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot exceeds its bound")
            encoded = path.read_bytes()
            if hashlib.sha256(encoded).hexdigest() != snapshot:
                raise ValueError("snapshot hash mismatch")
            data = json.loads(encoded)
            if not isinstance(data, dict) or data.get("version") != 1 or data.get("profile") != identity:
                raise ValueError("snapshot version/profile mismatch")
            if data.get("skill_claims") != claimed or data.get("perk_claims") != perk_claimed:
                raise ValueError("snapshot does not match the loaded game reward ledgers")
            profile = data["progress"]
            self._check_saved_claims(identity, profile, claimed, perk_claimed)
            return copy.deepcopy(profile)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProtocolError("Saved educational state is missing or corrupt; restore its matching progression-snapshots backup. Latest bridge progress was not imported. " + str(exc)[:180]) from exc

    def _activation_candidate(self, identity, activation, snapshot, restore):
        current = self.data.setdefault("activations", {}).get(identity)
        if current and current["activation"] == activation:
            return current, False
        if current and activation in current.get("retired", []):
            raise ProtocolError("Discarded progression timeline cannot be reactivated by a delayed request")
        if not restore:
            raise ProtocolError("No matching live progression timeline; load the game save to select its saved state")
        retired = list(current.get("retired", [])) if current else []
        if current:
            retired.append(current["activation"])
        if len(retired) > MAX_RETIRED_ACTIVATIONS:
            raise ProtocolError("Progression activation history reached its safety bound; preserve state for operator recovery")
        return {"activation": activation, "retired": retired, "restored_from": snapshot,
                "at": time.time()}, True

    def activate_progression(self, identity, activation, snapshot, restore, claimed, perk_claimed, values):
        _hex(identity, 32)
        _hex(activation, 32)
        _hex(snapshot, 64, allow_zero=True)
        if type(restore) is not bool:
            raise ProtocolError("invalid progression restore flag")
        validate_claims(claimed)
        validate_perk_claims(perk_claimed)
        with self.lock:
            _, replacing = self._activation_candidate(identity, activation, snapshot, restore)
            if not replacing:
                self._check_saved_claims(identity, self._profile(identity), claimed, perk_claimed)
                self.contexts[identity] = dict(values)
                return {"restored": False, "notice": ""}
        # Validate before cancelling work or touching the current timeline.
        if snapshot == ZERO_SNAPSHOT:
            profile = {"receipts": copy.deepcopy(claimed), "queues": {}, "attempt": None,
                       "perk_receipts": {key: {"profile": identity, "receipt": receipt,
                                               "recovered_from_game": True}
                                         for key, receipt in perk_claimed.items()}}
            self._check_saved_claims(identity, profile, claimed, perk_claimed)
            notice = "This save has no educational snapshot. Started an explicit baseline containing only its saved reward claims; later completions, queues and attempts were not imported."
        else:
            profile = self._read_progression_snapshot(identity, snapshot, claimed, perk_claimed)
            notice = "Progression restored from this game save."
        attempt = profile.get("attempt")
        if attempt and attempt.get("environment_kind") != "theory":
            old_id = attempt["id"]
            attempt["id"] = secrets.token_hex(16)
            attempt["generation"] = attempt.get("generation", 0) + 1
            attempt["status"] = "selected"
            attempt["restored_from_attempt"] = old_id
            attempt["practical"] = None
            attempt["checkpoints"] = []
            for key in ("machine_state", "facility_error", "last_facility_feedback"):
                attempt.pop(key, None)
            if attempt.get("conceptual"):
                attempt["conceptual"].update(attempt=attempt["id"], generation=attempt["generation"])
            notice += " Practical runtime/evidence was invalidated without learner penalty; R reissues the canonical environment. Saved question drafts/commitments remain."
        profile["updated"] = time.time()
        # One game/frontend uses this provider. A load of another character also
        # detaches its previous practical runtime; it is never a saved VM state.
        with self.lock:
            other_runtime = [key for key in self.environments if key != identity]
        for other in other_runtime:
            self.cancel(other)
            with self._operation(other), self.lock:
                self._remove_environment(other)
                previous = self._profile(other).get("attempt")
                if previous:
                    self._provider_stopped(previous)
                self._save()
        self.cancel(identity)
        with self._operation(identity):
            with self.lock:
                metadata, replacing = self._activation_candidate(identity, activation, snapshot, restore)
                if not replacing:
                    self._check_saved_claims(identity, self._profile(identity), claimed, perk_claimed)
                    return {"restored": False, "notice": ""}
            self._remove_environment(identity)
            with self.lock:
                old_profile = self.data["profiles"].get(identity)
                old_metadata = self.data["activations"].get(identity)
                self.data["profiles"][identity] = profile
                self.data["activations"][identity] = metadata
                self.contexts[identity] = dict(values)
                try:
                    self._save()
                except Exception:
                    if old_profile is None:
                        self.data["profiles"].pop(identity, None)
                    else:
                        self.data["profiles"][identity] = old_profile
                    if old_metadata is None:
                        self.data["activations"].pop(identity, None)
                    else:
                        self.data["activations"][identity] = old_metadata
                    raise
        return {"restored": True, "notice": notice}

    def capture_progression(self, identity, activation, claimed, perk_claimed, values):
        self.assert_activation(identity, activation)
        with self._operation(identity), self.lock:
            self.assert_activation(identity, activation)
            profile = self._profile(identity)
            self._check_saved_claims(identity, profile, claimed, perk_claimed)
            data = {"version": 1, "profile": identity, "progress": copy.deepcopy(profile),
                    "skill_claims": copy.deepcopy(claimed), "perk_claims": copy.deepcopy(perk_claimed)}
            encoded = _encoded(data)
            if len(encoded) > MAX_SNAPSHOT_BYTES:
                raise ProtocolError("Progression snapshot exceeds 8 MiB; game save cannot capture educational progress")
            snapshot = hashlib.sha256(encoded).hexdigest()
            directory = self.state_dir / "progression-snapshots"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / (snapshot + ".json")
            if path.exists():
                if path.read_bytes() != encoded:
                    raise ProtocolError("Existing progression snapshot is corrupt; preserve it for recovery")
            else:
                # A failed game save may leave an unreferenced immutable snapshot;
                # it can never change another save's selected progression.
                temporary = directory / (snapshot + ".tmp-" + secrets.token_hex(8))
                try:
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                if os.name != "nt":
                    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            self.contexts[identity] = dict(values)
            return snapshot
