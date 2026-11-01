import hashlib
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from challenge_content import Catalog
from challenge_env import ComposeEnvironment, POLICY_NEW, POLICY_OLD, PREPARED_WORKER
from training import OperationCancelled


class PreparedFixtureTests(unittest.TestCase):
    def make_env(self):
        catalog = Catalog()
        spec = replace(catalog.get("unarmed.files.make-directory"), kind="checkpoint-policy")
        env = ComposeEnvironment(mock.Mock(), spec, "prepared-unit", 7, 0,
                                 Path(tempfile.gettempdir()) / "prepared-unit-state")
        env.services = {"workstation": "a" * 64}
        env.container_id = env.services["workstation"]
        env.worker = {"directory": "/tmp/private-worker", "nonce": "one", "pid": 17, "start": "320"}
        env.client.api.inspect_container.return_value = {"State": {"Running": True}}
        return env

    def policy_snapshot(self, env, policy=POLICY_NEW, protected=None):
        def item(text):
            return {"regular": True, "directory": False, "uid": 1000, "gid": 1000,
                    "mode": 0o644, "text": text}
        return {"/home/nixboy/lab/" + name: item(text) for name, text in (
            ("policy.conf", policy), ("old-policy.conf", POLICY_OLD),
            ("new-policy.conf", POLICY_NEW),
            ("protected.txt", env.payload if protected is None else protected))}

    def test_worker_liveness_is_pinned_to_identity_and_progress(self):
        env = self.make_env()
        with mock.patch("challenge_env.time.sleep"), mock.patch.object(env, "_worker_probe", side_effect=[
            {"pid": 17, "start": "320", "ticks": 8},
            {"pid": 17, "start": "320", "ticks": 9},
        ]):
            result = env.health()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["evidence"]["worker"]["ticks"], 9)
        for first, second in (
            ({"pid": 17, "start": "320", "ticks": 9}, {"pid": 17, "start": "320", "ticks": 9}),
            ({"pid": 17, "start": "320", "ticks": 9}, {"pid": 17, "start": "999", "ticks": 10}),
            ({"pid": 17, "start": "320", "ticks": 9}, {"pid": 18, "start": "320", "ticks": 10}),
        ):
            with self.subTest(first=first, second=second), mock.patch("challenge_env.time.sleep"), \
                    mock.patch.object(env, "_worker_probe", side_effect=[first, second]):
                result = env.health()
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["facility_failure"])

    def test_stopped_container_and_lost_worker_are_facility_errors(self):
        env = self.make_env()
        env.client.api.inspect_container.return_value = {"State": {"Running": False}}
        self.assertTrue(env.check()["facility_failure"])
        env.client.api.inspect_container.return_value = {"State": {"Running": True}}
        with mock.patch.object(env, "_worker_probe", return_value={"error": "worker died"}):
            result = env.checkpoint("rollback")
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["facility_failure"])

    def test_checkpoint_uses_actual_policy_and_preservation_not_claimed_name(self):
        env = self.make_env()
        ready = {"status": "ready", "evidence": {"worker": {"pid": 17, "start": "320", "ticks": 10}}}
        with mock.patch.object(env, "health", return_value=ready), \
                mock.patch.object(env, "snapshot", return_value=self.policy_snapshot(env)):
            self.assertEqual(env.checkpoint("rollback")["status"], "incomplete")
            new = env.checkpoint("reapply")
            self.assertEqual(new["status"], "passed")
            self.assertEqual(new["evidence"]["policy"]["sha256"], hashlib.sha256(POLICY_NEW.encode()).hexdigest())
        with mock.patch.object(env, "health", return_value=ready), \
                mock.patch.object(env, "snapshot", return_value=self.policy_snapshot(env, POLICY_OLD)):
            self.assertEqual(env.checkpoint("rollback")["status"], "passed")
            self.assertEqual(env.checkpoint("reapply")["status"], "incomplete")
        with mock.patch.object(env, "health", return_value=ready), \
                mock.patch.object(env, "snapshot", return_value=self.policy_snapshot(env, POLICY_OLD, "changed")):
            self.assertEqual(env.checkpoint("rollback")["status"], "incomplete")

    def test_unknown_checkpoint_cannot_trigger_a_probe(self):
        env = self.make_env()
        with mock.patch.object(env, "health") as health:
            self.assertEqual(env.checkpoint("arbitrary-shell-command")["status"], "error")
            health.assert_not_called()

    def test_worker_launch_has_no_attachment_or_learner_shell_ownership(self):
        env = self.make_env()
        env.client.api.exec_create.return_value = {"Id": "independent-worker"}
        with mock.patch.object(env, "_worker_probe", return_value={"pid": 17, "start": "320", "ticks": 1}):
            env._start_prepared_worker()
        args = env.client.api.exec_create.call_args
        self.assertEqual(args.kwargs["user"], "0:0")
        self.assertEqual(args.kwargs["workdir"], "/tmp")
        self.assertEqual(args.args[1][:5], ["/usr/bin/python3", "-I", "-u", "-c", PREPARED_WORKER])
        self.assertNotIn("NIXBOY_ATTACHMENT_TOKEN", args.kwargs["environment"])
        env.client.api.exec_start.assert_called_once_with("independent-worker", detach=True)
        self.assertEqual(env.worker["pid"], 17)

    def test_cancelled_preparation_cleans_provisional_resources(self):
        env = self.make_env()
        cancel = threading.Event()
        calls = []
        def compose(*args, **kwargs):
            calls.append(args)
            if args[0] == "up":
                cancel.set()
            return "a" * 64
        with mock.patch.object(env, "_compose", side_effect=compose), self.assertRaises(OperationCancelled):
            env.start(cancel=cancel)
        self.assertTrue(any(call[0] == "down" for call in calls))
        self.assertFalse(env.services)
        self.assertIsNone(env.container_id)
        self.assertIsNone(env.worker)


if __name__ == "__main__":
    unittest.main()
