"""Current-run attempts and one-completion delivery, with no achievement replay."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training import AttemptManager
from protocol import ProtocolError, training_context
from test_training import FakeCatalog, FakeEnvironment, context, claim
from test_bridge import FakeClient


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environments = []
        def create(*args, **kwargs):
            result = FakeEnvironment(*args, **kwargs)
            self.environments.append(result)
            return result
        self.manager = AttemptManager(self.directory.name, FakeClient, FakeCatalog(), create)
        self.addCleanup(self.manager.close)
        self.identity, _ = training_context(context())
        self.manager.observe(self.identity, {}, context())

    def other(self):
        return "22"*16

    def test_observed_native_rewards_are_not_imported_or_replayed(self):
        claimed = {"3:1": claim("33"*16)}
        self.manager.observe(self.identity, claimed, context())
        self.assertNotIn("receipts", self.manager.snapshot(self.identity))
        self.assertFalse(self.manager.pending_rewards(self.identity, {}))
        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.manager.select(self.identity, 3, 1)
        self.manager.observe(self.other(), {}, context())
        self.manager.select(self.other(), 3, 1)
        self.assertFalse(self.manager.pending_rewards(self.other(), {}))

    def test_same_runtime_reopen_preserves_attempt_environment_and_queue(self):
        selected = self.manager.select(self.identity, 3, 1)
        self.manager.prepare(self.identity)
        before = self.manager.snapshot(self.identity)
        self.manager.detach_frontend(self.identity)
        self.manager.observe(self.identity, {}, context())
        self.manager.attach_frontend(self.identity)
        after = self.manager.snapshot(self.identity)
        self.assertEqual(after["attempt"]["id"], selected["id"])
        self.assertEqual(after["queues"], before["queues"])
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(len(self.environments), 1)

    def test_one_pending_completion_retries_until_ack_then_cannot_restore(self):
        self.manager.select(self.identity, 3, 1)
        self.manager.prepare(self.identity)
        self.assertTrue(self.manager.check(self.identity)["rewarded"])
        pending = self.manager.pending_rewards(self.identity, {})
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.manager.pending_rewards(self.identity, {}), pending)
        with self.assertRaisesRegex(ValueError, "awaiting game"):
            self.manager.select(self.identity, 12, 1)
        for _ in range(2):
            self.manager.acknowledge(self.identity, *pending[0], True)
        self.assertFalse(self.manager.pending_rewards(self.identity, {}))
        self.manager.observe(self.identity, {}, context())
        self.assertFalse(self.manager.pending_rewards(self.identity, {}))
        self.manager.observe(self.other(), {}, context())
        self.assertFalse(self.manager.pending_rewards(self.other(), {}))
        self.manager.select(self.other(), 3, 1)
        with self.assertRaises(ProtocolError):
            self.manager.acknowledge(self.identity, *pending[0], True)

    def test_failed_save_cannot_publish_an_uncommitted_completion(self):
        selected = self.manager.select(self.identity, 3, 1)
        self.manager.prepare(self.identity)
        with mock.patch.object(self.manager, "_save", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.manager.check(self.identity)
        self.assertIsNone(self.manager.snapshot(self.identity)["pending"])
        self.assertEqual(self.manager.snapshot(self.identity)["attempt"]["id"], selected["id"])

    def test_scope_change_cancels_late_validation_and_cleans_only_old_runtime(self):
        self.manager.select(self.identity, 3, 1)
        self.manager.prepare(self.identity)
        entered, release = threading.Event(), threading.Event()
        def late_check():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture timeout")
            return {"status": "passed"}
        self.environments[-1].check = late_check
        results = []
        checking = threading.Thread(target=lambda: results.append(self.manager.check(self.identity)))
        checking.start(); self.assertTrue(entered.wait(2))
        observing = threading.Thread(target=lambda: self.manager.observe(self.other(), {}, context()))
        observing.start()
        self.assertTrue(self.manager.cancel_events[self.identity].wait(2))
        release.set(); checking.join(3); observing.join(3)
        self.assertFalse(checking.is_alive() or observing.is_alive())
        self.assertEqual(results[0]["status"], "error")
        self.assertTrue(self.environments[-1].closed)
        self.assertIsNone(self.manager.snapshot(self.other())["pending"])
        with self.assertRaises(ProtocolError):
            self.manager.select(self.identity, 3, 1)

    def test_minimal_cache_rejects_previous_progression_format_and_bad_project(self):
        self.manager.close()
        for data in ({"version": 4, "profiles": {}},
                     {"scope":"nixboy-runtime", "version":1, "profiles":{self.identity:{
                         "queues":{}, "assessment_queues":{}, "attempt":None,
                         "project":{"id":"project", "next_stage":-1}, "last_assessment":None, "pending":None}}}):
            self.manager.path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                AttemptManager(self.directory.name, FakeClient, FakeCatalog(), FakeEnvironment)
        self.manager.data = {"scope":"nixboy-runtime", "version":1, "profiles":{}}


if __name__ == "__main__":
    unittest.main()
