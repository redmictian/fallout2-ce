from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol as wire
from inspect_training_save import inspect


def context(version=5):
    values = {"training.version": version, **{"skill."+name: 10 for name in wire.SKILL_NAMES}}
    values.update({f"training.profile.{index}": 1 for index in range(4)})
    return values


class TypedPerkWireTests(unittest.TestCase):
    def test_v4_open_stays_identical_and_v5_adds_distinct_ledger(self):
        skill = {"3:1": {"receipt": "11" * 16, "points": 5}}
        old = wire.encode_training_open(40, 153, context(4), skill)
        self.assertEqual(wire.decode_training_open(old, include_perks=True), (40, 153, context(4), skill, {}))
        perks = {"12:2": "22" * 16}
        new = wire.encode_training_open(40, 153, context(), skill, perks)
        self.assertEqual(wire.decode_training_open(new, include_perks=True), (40, 153, context(), skill, perks))
        self.assertEqual(wire.decode_training_open(new), (40, 153, context(), skill))
        self.assertEqual(wire.encode_training_open(40, 153, context(4), skill, {}), old)
        with self.assertRaises(wire.ProtocolError):
            wire.encode_training_open(40, 153, context(4), {}, perks)

    def test_cross_kind_receipt_duplicates_and_bad_perk_claims_reject(self):
        for claims in ({"13:2": "22" * 16}, {"12:3": "22" * 16}, {"12:2": "00" * 16},
                       {"12:2": {"receipt": "22" * 16}}, {"12:2": "AA" * 16}):
            with self.assertRaises(wire.ProtocolError):
                wire.validate_perk_claims(claims)
        with self.assertRaises(wire.ProtocolError):
            wire.encode_training_open(40, 153, context(), {"3:1": {"receipt": "22" * 16, "points": 1}}, {"12:2": "22" * 16})
        valid = wire.encode_training_open(40, 153, context(), {}, {"12:2": "22" * 16})
        for invalid in (valid[:-1], valid+b"x", valid[:-21]+b"\x00\x02"+valid[-19:]*2):
            with self.assertRaises(wire.ProtocolError):
                wire.decode_training_open(invalid, include_perks=True)

    def test_profile_bound_reward_and_ack_roundtrip(self):
        profile, receipt = "11" * 16, bytes.fromhex("22" * 16)
        payload = wire.encode_training_perk_reward(profile, 12, 2, receipt)
        self.assertEqual(len(payload), 35)
        self.assertEqual(wire.decode_training_perk_reward(payload), (profile, 12, 2, receipt.hex()))
        self.assertEqual(wire.decode_training_perk_ack(payload+b"\x01"), (profile, 12, 2, receipt.hex(), True))
        self.assertFalse(wire.decode_training_perk_ack(payload+b"\x00")[-1])
        for invalid in (payload, payload+b"\x02", payload+b"\x01x"):
            with self.assertRaises(wire.ProtocolError):
                wire.decode_training_perk_ack(invalid)
        for args in ((bytes(16),12,2,receipt), (profile,13,2,receipt), (profile,12,3,receipt), (profile,12,2,bytes(16))):
            with self.assertRaises(wire.ProtocolError):
                wire.encode_training_perk_reward(*args)

    def test_actual_save_inspector_reads_v5_and_rejects_conflicts(self):
        skill = struct.pack("!BHH", 3, 1, 5) + b"\x11" * 16
        def trailer(receipt=b"\x22" * 16):
            payload = b"\xaa" * 16 + b"\x00\x01" + skill + b"\x00\x01" + struct.pack("!HB", 12, 2) + receipt
            data = b"NXBTRAIN" + struct.pack("!II", 5, len(payload)) + payload
            return data + struct.pack("!I", zlib.crc32(data))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SAVE.DAT"
            path.write_bytes(b"opaque game body" + trailer())
            result = inspect(path)
            self.assertEqual(result["version"], 5)
            self.assertEqual(result["perk_claims"], {"12:2": "22" * 16})
            self.assertEqual(result["bonuses"], {"unarmed": 5})
            path.write_bytes(b"opaque game body" + trailer(b"\x11" * 16))
            with self.assertRaises(ValueError):
                inspect(path)


if __name__ == "__main__":
    unittest.main()
