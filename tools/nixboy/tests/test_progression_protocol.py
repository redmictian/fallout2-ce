from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol as wire
from inspect_training_save import inspect


def context(version=6):
    return {"training.version": version, **{"skill."+name: 10 for name in wire.SKILL_NAMES},
            **{f"training.profile.{index}": 1 for index in range(4)}}


class SaveProgressionWireTests(unittest.TestCase):
    def test_v6_binds_snapshot_and_activation_without_changing_legacy_open(self):
        binding = {"activation": "11"*16, "snapshot": "22"*32, "restore": True}
        payload = wire.encode_training_open(35, 139, context(), {}, {}, binding=binding)
        self.assertEqual(wire.decode_training_open(payload, include_binding=True),
                         (35, 139, context(), {}, {}, binding))
        for version in (4, 5):
            old = wire.encode_training_open(35, 139, context(version), {})
            self.assertIsNone(wire.decode_training_open(old, include_binding=True)[-1])
        for invalid in (payload[:-1], payload+b"x", payload[:-1]+b"\x02"):
            with self.assertRaises(wire.ProtocolError):
                wire.decode_training_open(invalid, include_binding=True)
        for invalid in (None, {**binding, "activation": "00"*16}, {**binding, "snapshot": "bad"},
                        {**binding, "restore": 1}):
            with self.assertRaises(wire.ProtocolError):
                wire.encode_training_open(35, 139, context(), {}, binding=invalid)

    def test_activation_envelope_rejects_discarded_timeline_and_missing_prefix(self):
        body = wire.encode_training_perk_reward("33"*16, 12, 2, bytes.fromhex("44"*16))
        fenced = wire.fence_training_payload(body, "11"*16)
        self.assertEqual(wire.unfence_training_payload(fenced, "11"*16), body)
        for payload, activation in ((fenced, "22"*16), (body, "11"*16), (b"", "11"*16)):
            with self.assertRaises(wire.ProtocolError):
                wire.unfence_training_payload(payload, activation)

    def test_v6_inspector_reads_exact_saved_snapshot_and_empty_perk_ledger(self):
        for perk_record in (b"", struct.pack("!HB", 12, 2)+b"\x44"*16):
            body = b"\x33"*16 + b"\x00\x00" + struct.pack("!H", bool(perk_record)) + perk_record + b"\x22"*32
            trailer = b"NXBTRAIN" + struct.pack("!II", 6, len(body)) + body
            trailer += struct.pack("!I", zlib.crc32(trailer))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"SAVE.DAT"
                path.write_bytes(b"opaque game records"+trailer)
                result = inspect(path)
                self.assertEqual(result["progression_snapshot"], "22"*32)
                self.assertEqual(result["perk_claims"], {"12:2": "44"*16} if perk_record else {})
                path.write_bytes(b"opaque game records"+trailer[:-5]+b"x"+trailer[-4:])
                with self.assertRaises(ValueError):
                    inspect(path)


if __name__ == "__main__":
    unittest.main()
