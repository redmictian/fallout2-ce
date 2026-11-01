from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspect_training_save import inspect


def trailer(claims=()):
    rows = b"".join(struct.pack("!BHH", skill, level, points) + receipt
                    for skill, level, points, receipt in claims)
    data = b"NXBTRAIN" + struct.pack("!II", 4, 18 + len(rows)) + b"\x11" * 16 \
        + struct.pack("!H", len(claims)) + rows
    return data + struct.pack("!I", zlib.crc32(data))


class SaveInspectionTests(unittest.TestCase):
    def test_old_and_rewarded_save_ledgers_with_same_identity(self):
        with tempfile.TemporaryDirectory() as name:
            old = Path(name) / "old.dat"
            earned = Path(name) / "earned.dat"
            old.write_bytes(b"opaque game body" + trailer())
            earned.write_bytes(b"opaque game body" + trailer([(12, 60000, 1, b"\x22" * 16)]))
            before = old.read_bytes(), earned.read_bytes()
            a, b = inspect(old), inspect(earned)
            self.assertEqual(a["profile"], b["profile"])
            self.assertEqual(a["claims"], {})
            self.assertEqual(b["claims"]["12:60000"]["points"], 1)
            self.assertEqual(b["bonuses"], {"science": 1})
            self.assertEqual(before, (old.read_bytes(), earned.read_bytes()))

    def test_refuses_corrupt_or_ambiguous_ledger(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "SAVE.DAT"
            for data in (trailer()[:-1], trailer() + b"extra",
                         trailer([(12, 60000, 1, b"\x22" * 16)] * 2)):
                path.write_bytes(b"opaque game body" + data)
                with self.assertRaises(ValueError):
                    inspect(path)


if __name__ == "__main__":
    unittest.main()
