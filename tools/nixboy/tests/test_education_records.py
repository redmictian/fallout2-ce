import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from education_records import export_snapshot, read_snapshot


class EducationBackupTests(unittest.TestCase):
    def test_atomic_snapshot_export_is_private_nonoverwriting_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"version": 4, "profiles": {"01" * 16: {
                "receipts": {}, "queues": {}, "attempt": None,
                "education": {"version": 1, "completions": {"test.intro": {"passed": True}},
                              "queues": {}, "seen": {}, "history": []}}}}
            (root / "training.json").write_text(json.dumps(state))
            backup = root / "backup.json"
            self.assertEqual(export_snapshot(root, backup)["profiles"], 1)
            self.assertEqual(read_snapshot(backup), state)
            self.assertEqual(read_snapshot(root / "training.json"), state)
            with self.assertRaises(FileExistsError):
                export_snapshot(root, backup)
            with self.assertRaises(ValueError):
                export_snapshot(root, root / "training.json")

    def test_unknown_education_version_and_broken_receipts_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            for profile in ({"receipts": {}, "education": {"version": 2}},
                            {"receipts": {"3:1": {"receipt": "00" * 16, "points": 1}}}):
                path.write_text(json.dumps({"version": 4, "profiles": {"01" * 16: profile}}))
                with self.assertRaises(ValueError):
                    read_snapshot(path)


if __name__ == "__main__":
    unittest.main()
