"""One current-completion passive-perk delivery, never an achievement ledger."""
from protocol import ProtocolError, validate_perk_claims


class PerkRewards:
    def _perk_eligibility(self, identity, reward):
        if reward is None:
            return
        values = self.contexts.get(identity, {})
        if values.get("training.version") != 5 or values.get("perk.training.enabled") != 1:
            raise ValueError("The passive perk-capable build must be enabled")
        if values.get("perk.toughness") != 1 or values.get("perk.toughness.max", 0) < 2:
            raise ValueError("Own inert Toughness rank 1 before this test-only reward assessment")

    def _earn_perk(self, identity, gate, attempt):
        if gate.reward is None:
            return False
        self._perk_eligibility(identity, gate.reward)
        profile = self._profile(identity)
        self._pending_guard(profile)
        profile["pending"] = {"kind": "perk", "profile": identity,
            "perk": gate.reward["perk"], "rank": gate.reward["target_rank"],
            "receipt": self._new_receipt(identity), "accepted": False}
        return True

    def observe_perks(self, identity, claimed):
        validate_perk_claims(claimed)
        with self.lock:
            if set(claimed.values()) & {item["receipt"] for item in self.game_claims.get(identity, {}).values()}:
                raise ProtocolError("receipt reused across character reward types")
            self.game_perk_claims[identity] = dict(claimed)
            pending = self._profile(identity).get("pending")
            if pending and pending["kind"] == "perk":
                key = f"{pending['perk']}:{pending['rank']}"
                if key in claimed:
                    if claimed[key] != pending["receipt"]:
                        raise ProtocolError("current perk completion conflicts with character claim")
                    pending["accepted"] = True
            self._save()

    def pending_perks(self, identity, claimed):
        validate_perk_claims(claimed)
        with self.lock:
            pending = self._profile(identity).get("pending")
            if not pending or pending["kind"] != "perk" or pending["accepted"]:
                return []
            key = f"{pending['perk']}:{pending['rank']}"
            if key in claimed:
                if claimed[key] != pending["receipt"]:
                    raise ProtocolError("current perk completion conflicts with character claim")
                pending["accepted"] = True
                self._save()
                return []
            return [(identity, pending["perk"], pending["rank"], pending["receipt"])]

    def acknowledge_perk(self, identity, profile, perk, rank, receipt, accepted):
        if profile != identity:
            raise ProtocolError("perk acknowledgment runtime mismatch")
        validate_perk_claims({f"{perk}:{rank}": receipt})
        with self.lock:
            pending = self._profile(identity).get("pending")
            expected = {"kind": "perk", "profile": identity, "perk": perk, "rank": rank, "receipt": receipt}
            if not pending or any(pending.get(key) != value for key, value in expected.items()):
                raise ProtocolError("perk acknowledgment does not match the current completion")
            if accepted:
                pending["accepted"] = True
                self.game_perk_claims.setdefault(identity, {})[f"{perk}:{rank}"] = receipt
                self._save()
