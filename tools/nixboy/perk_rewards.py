"""Typed passive-perk receipt adapter for the existing bridge-owned manager."""
import secrets
import time

from protocol import ProtocolError, validate_perk_claims


class PerkRewards:
    def _perk_records(self, profile):
        return profile.setdefault("perk_receipts", {})

    def _perk_eligibility(self, identity, reward):
        if reward is None:
            return
        values = self.contexts.get(identity, {})
        key = f"{reward['perk']}:{reward['target_rank']}"
        if values.get("training.version") != 5:
            raise ValueError("This test reward needs the v5 perk-capable review build")
        if values.get("perk.training.enabled") != 1:
            raise ValueError("The passive perk training module must be enabled in this review build")
        if key not in self._perk_records(self._profile(identity)):
            if values.get("perk.toughness") != 1 or values.get("perk.toughness.max", 0) < 2:
                raise ValueError("Own inert Toughness rank 1 before starting this test-only reward assessment")

    def _earn_perk(self, identity, gate, attempt):
        if gate.reward is None:
            return False
        self._perk_eligibility(identity, gate.reward)
        profile = self._profile(identity)
        records = self._perk_records(profile)
        key = f"{gate.reward['perk']}:{gate.reward['target_rank']}"
        if key in records:
            return False
        used = {entry["receipt"] for entry in profile["receipts"].values()}
        used.update(entry["receipt"] for entry in records.values())
        receipt = secrets.token_hex(16)
        while receipt in used:
            receipt = secrets.token_hex(16)
        records[key] = {"profile": identity, "receipt": receipt, "gate": gate.id,
                        "attempt": attempt["id"], "generation": attempt["generation"],
                        "content": attempt["course_digest"], "at": time.time(), "test_only": True}
        return True  # The caller fsyncs this with educational completion before delivery.

    def observe_perks(self, identity, claimed):
        validate_perk_claims(claimed)
        with self.lock:
            profile = self._profile(identity)
            records = self._perk_records(profile)
            skill_receipts = {entry["receipt"] for entry in profile["receipts"].values()}
            for key, receipt in claimed.items():
                if receipt in skill_receipts:
                    raise ProtocolError("receipt reused across skill and perk rewards")
                if key in records and records[key]["receipt"] != receipt:
                    raise ProtocolError("saved perk receipt conflicts with earned state")
                if key not in records:
                    records[key] = {"profile": identity, "receipt": receipt, "recovered_from_game": True}
            self._save()

    def pending_perks(self, identity, claimed):
        validate_perk_claims(claimed)
        with self.lock:
            result = []
            for key, record in self._perk_records(self._profile(identity)).items():
                if record["profile"] != identity:
                    raise ProtocolError("perk receipt is bound to a different profile")
                if key in claimed:
                    if claimed[key] != record["receipt"]:
                        raise ProtocolError("game perk claim conflicts with earned receipt")
                    continue
                perk, rank = map(int, key.split(":"))
                result.append((identity, perk, rank, record["receipt"]))
            return result

    def acknowledge_perk(self, identity, profile, perk, rank, receipt, accepted):
        if profile != identity:
            raise ProtocolError("perk acknowledgment profile mismatch")
        validate_perk_claims({f"{perk}:{rank}": receipt})
        with self.lock:
            entry = self._perk_records(self._profile(identity)).get(f"{perk}:{rank}")
            if entry is None or entry["receipt"] != receipt or entry["profile"] != identity:
                raise ProtocolError("perk acknowledgment is not an earned receipt")
            if accepted:
                entry["acknowledged"] = True
                self._save()
