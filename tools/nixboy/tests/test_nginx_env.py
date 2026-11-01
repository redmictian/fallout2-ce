import copy
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nginx_env import NginxEnvironment, preparation_identity, digest_artifacts
from nginx_profile import ORIGINS, PUBLIC, STAFF, READY, DENIED, HOST_ID, configuration


class NativeServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        spec = SimpleNamespace(kind="qemu-nginx", parameters={"name": ["test"], "preparation": ["nginx-policy-v1"]})
        self.env = NginxEnvironment(SimpleNamespace(), spec, "native-test", 1, 0, self.directory.name)
        responses = {}
        logs = []
        for role, origin in ORIGINS.items():
            responses[role] = {}
            for path, body in (("/public.txt", PUBLIC), ("/staff.txt", STAFF if role == "allowed" else DENIED), ("/stage2", READY)):
                marker = role + path
                status = 403 if role == "denied" and path == "/staff.txt" else 200
                responses[role][path] = {"code": 0, "status": status, "body": body, "host_id": HOST_ID, "marker": marker}
                logs.append({"probe": marker, "source": origin["address"], "status": status})
        self.probe = {"boot_id": "boot-a", "pid1": "systemd", "normal": {"stdout": "active"},
                      "journal_socket": True, "journal": {"code": 0, "stdout": "Reloaded nginx.service"},
                      "nginx_test": {"code": 0}, "nginx_configuration": {"code": 0},
                      "service": {"ActiveState": "active", "UnitFileState": "enabled", "MainPID": "10", "ExecReload": "pid=44 status=0"},
                      "workers": [{"pid": 20, "uid": ["33"] * 4, "gid": ["33"] * 4, "cmd": "nginx: worker process"}],
                      "requests": responses, "logs": logs, "files": {}}
        for name, text in (("public.txt", PUBLIC), ("staff.txt", STAFF), ("stage1.reference", "canonical stage one contract v1\n")):
            self.probe["files"][name] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "uid": 0, "gid": 0, "mode": 0o644, "mtime": 1768478400}

    def tearDown(self):
        self.directory.cleanup()

    def test_valid_policy_uses_full_bodies_sources_manager_and_credentials(self):
        self.assertTrue(all(self.env._decide(self.probe).values()))
        self.probe["requests"]["denied"]["/staff.txt"]["body"] = "connection failed"
        self.assertFalse(self.env._decide(self.probe)["denied_staff"])

    def test_initial_open_policy_is_unsolved_but_canonically_valid(self):
        self.probe["requests"]["denied"]["/staff.txt"].update(status=200, body=STAFF)
        self.assertTrue(all(self.env._decide(self.probe, initial=True).values()))
        self.assertFalse(self.env._decide(self.probe)["denied_staff"])

    def test_root_worker_workaround_does_not_pass(self):
        self.probe["workers"][0]["uid"] = ["0"] * 4
        self.assertFalse(self.env._decide(self.probe)["nonprivileged_workers"])
        self.assertTrue(all(self.env._facility_checks(self.probe).values()))

    def test_broken_client_wrong_host_and_stopped_service_are_facility_errors(self):
        for mutation in ("client", "host", "server"):
            value = copy.deepcopy(self.probe)
            if mutation == "client":
                value["requests"]["denied"]["/public.txt"]["code"] = 7
            elif mutation == "host":
                value["requests"]["allowed"]["/public.txt"]["host_id"] = "wrong-server"
            else:
                value["service"]["ActiveState"] = "inactive"
            with mock.patch.object(self.env, "_probe", return_value=value):
                result = self.env.check()
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["facility_failure"])

    def test_identical_client_source_log_and_corrupt_protected_file_fail(self):
        self.probe["logs"] = [{**item, "source": "10.77.0.2"} for item in self.probe["logs"]]
        self.probe["files"]["staff.txt"]["sha256"] = "0" * 64
        checks = self.env._decide(self.probe)
        self.assertFalse(checks["distinct_client_sources"])
        self.assertFalse(checks["protected_staff.txt"])

    def test_wrong_public_bytes_on_a_capable_client_are_objective_nonpass(self):
        self.probe["requests"]["allowed"]["/public.txt"]["body"] = "learner changed public payload\n"
        self.assertTrue(all(self.env._facility_checks(self.probe).values()))
        with mock.patch.object(self.env, "_probe", return_value=self.probe):
            result = self.env.check()
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["evidence"]["checks"]["allowed_public_body"])
        self.assertNotIn("facility_failure", result)

    def test_reload_requires_same_master_replaced_workers_native_manager_and_boot(self):
        self.env.initial_service = {"main": "10", "workers": [19], "boot_id": "boot-a"}
        self.env._last_probe = self.probe
        with mock.patch.object(self.env, "check", return_value={"status": "passed"}):
            self.assertEqual(self.env.checkpoint("reload")["status"], "passed")
            self.probe["service"]["MainPID"] = "11"
            self.assertEqual(self.env.checkpoint("reload")["status"], "incomplete")
            self.probe["service"]["MainPID"] = "10"
            self.probe["workers"][0]["pid"] = 19
            self.assertEqual(self.env.checkpoint("reload")["status"], "incomplete")

    def test_fixed_profiles_and_preparation_identity_are_bounded(self):
        with self.assertRaises(ValueError):
            preparation_identity("arbitrary-command")
        self.assertNotEqual(preparation_identity("nginx-policy-v1"), preparation_identity("nginx-reload-v1"))
        self.assertEqual(len(digest_artifacts()), 64)
        self.assertIn("allow all;", configuration("nginx-policy-v1"))
        self.assertIn("allow 10.77.0.2; deny all;", configuration("nginx-reload-v1"))


if __name__ == "__main__":
    unittest.main()
