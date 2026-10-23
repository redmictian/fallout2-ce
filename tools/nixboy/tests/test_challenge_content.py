import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge_content import Catalog, CONTENT_ROOT
from challenge_env import ComposeEnvironment, cleanup_owned, owner_namespace


class CatalogTests(unittest.TestCase):
    def test_twenty_distinct_tasks_in_four_fixed_competency_pools(self):
        catalog = Catalog()
        self.assertEqual(set(catalog.tracks), {3, 12})
        self.assertEqual(len(catalog.challenges), 20)
        for skill in (3, 12):
            for level in (1, 2):
                pool = catalog.pool(skill, level)
                self.assertEqual(len(pool), 5)
                self.assertEqual(len({catalog.get(identifier).kind for identifier in pool}), 5)
        self.assertEqual(catalog.pool(4, 1), [])

    def test_seeded_bounded_parameters_and_formattable_plaintext(self):
        catalog = Catalog()
        for spec in catalog.challenges.values():
            seen = set()
            for seed in range(100):
                params = catalog.instantiate(spec.id, seed)
                self.assertEqual(params, catalog.instantiate(spec.id, seed))
                self.assertTrue(all(value in spec.parameters[key] for key, value in params.items()))
                seen.add(json.dumps(params, sort_keys=True))
                self.assertNotIn("{", spec.instructions.format(**params))
            self.assertGreater(len(seen), 1)
        for seed in (-1, 2**63, True, "1"):
            with self.assertRaises(ValueError):
                catalog.instantiate(next(iter(catalog.challenges)), seed)

    def test_skill_bands_include_boundaries_without_introductory_fallback(self):
        catalog = Catalog()
        for skill, boundaries in ((3, ((14, None), (15, 1), (19, 1), (20, 2), (24, 2), (25, None))),
                                  (12, ((9, None), (10, 1), (14, 1), (15, 2), (19, 2), (20, None)))):
            for value, expected in boundaries:
                with self.subTest(skill=skill, value=value):
                    self.assertEqual(catalog.level_for_value(skill, value), expected)
        self.assertIsNone(catalog.level_for_value(4, 15))
        self.assertIsNone(catalog.level_for_value(3, None))

    def test_catalog_has_task_instructions_without_embedded_teaching_materials(self):
        catalog = Catalog()
        for spec in catalog.challenges.values():
            self.assertFalse(hasattr(spec, "lesson"))
            self.assertTrue(spec.instructions)


class EnvironmentBoundaryTests(unittest.TestCase):
    def make_env(self, identifier="science.network.ssh-upload", runner=None):
        catalog = Catalog()
        return ComposeEnvironment(mock.Mock(), catalog.get(identifier), "attempt-1", 5, 0,
                                  Path(tempfile.gettempdir()) / "nixboy-unit-state", runner=runner)

    def test_fixed_compose_local_endpoint_and_no_shell(self):
        runner = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
        env = self.make_env(runner=runner)
        with mock.patch.dict("os.environ", {"COMPOSE_FILE": "attacker.yaml", "DOCKER_HOST": "tcp://remote", "NIXBOY_OWNER": "wrong"}):
            env._compose("up", "--wait")
        argv = runner.call_args.args[0]
        options = runner.call_args.kwargs
        self.assertEqual(argv[0], "docker")
        self.assertIn("network.yaml", argv[argv.index("--file") + 1])
        self.assertNotIn("shell", options)
        self.assertNotIn("COMPOSE_FILE", options["env"])
        self.assertNotIn("DOCKER_HOST", options["env"])
        self.assertEqual(options["env"]["NIXBOY_OWNER"], env.owner)
        self.assertLessEqual(options["timeout"], 45)

    def test_topologies_have_isolated_writable_executable_homes_and_no_host_access(self):
        for topology in ("single", "network"):
            data = yaml.safe_load((CONTENT_ROOT / "compose" / (topology + ".yaml")).read_text(encoding="utf-8"))
            self.assertEqual(len(data["services"]), 1 if topology == "single" else 2)
            for service in data["services"].values():
                self.assertTrue(service["read_only"])
                self.assertEqual(service["pull_policy"], "never")
                self.assertNotIn("volumes", service)
                self.assertNotIn("ports", service)
                self.assertNotIn("privileged", service)
                home = next(value for value in service["tmpfs"] if value.startswith("/home/nixboy:"))
                for option in ("rw", "exec", "nosuid", "nodev"):
                    self.assertIn(option, home.split(":", 1)[1].split(","))
            if topology == "network":
                self.assertTrue(data["networks"]["lab"]["internal"])
            else:
                self.assertEqual(data["services"]["workstation"]["network_mode"], "none")

    def test_exception_during_validation_is_error_not_incomplete(self):
        env = self.make_env()
        env.services = {"workstation": "a", "server": "b"}
        env.client.api.inspect_container.return_value = {"State": {"Running": True}}
        with mock.patch.object(env, "_validate", side_effect=RuntimeError("probe crashed")):
            self.assertEqual(env.check()["status"], "error")
        with mock.patch.object(env, "_validate", return_value=(False, "keep trying")):
            self.assertEqual(env.check()["status"], "incomplete")
        env.client.api.inspect_container.return_value = {"State": {"Running": False}}
        self.assertEqual(env.check()["status"], "error")

    def test_cleanup_scoped_by_training_and_state_namespace(self):
        client = mock.Mock()
        client.api.containers.return_value = [{"Id": "owned-container"}]
        client.api.networks.return_value = [{"Id": "owned-network"}]
        cleanup_owned(client, "state")
        filters = client.api.containers.call_args.kwargs["filters"]
        self.assertEqual(filters["label"], ["org.fallout2-ce.nixboy.training=1", "org.fallout2-ce.nixboy.owner=" + owner_namespace("state")])
        client.api.remove_container.assert_called_once_with("owned-container", force=True, v=True)
        client.api.remove_network.assert_called_once_with("owned-network")

    def test_invalid_restored_parameters_rejected_before_docker(self):
        catalog = Catalog()
        client = mock.Mock()
        with self.assertRaises(ValueError):
            ComposeEnvironment(client, catalog.get("unarmed.files.copy-file"), "a", 1, 0, "state", params={"name": "../../host"})
        self.assertEqual(client.mock_calls, [])

    def test_exec_caps_stream_output_and_uses_immutable_timeout(self):
        env = self.make_env()
        env.services = {"workstation": "a"}
        env.client.api.exec_create.return_value = {"Id": "exec"}
        env.client.api.exec_start.return_value = iter([(b"x" * 65536, None)] * 3)
        with self.assertRaisesRegex(RuntimeError, "output limit"):
            env._exec("workstation", ["/usr/bin/ssh", "server"])
        argv = env.client.api.exec_create.call_args.args[1]
        self.assertEqual(argv[:5], ["/usr/bin/timeout", "--signal=TERM", "--kill-after=1", "7", "/usr/bin/ssh"])
        self.assertTrue(env.client.api.exec_start.call_args.kwargs["stream"])
        env.client.api.exec_start.return_value = iter([])
        env.client.api.exec_inspect.return_value = {"ExitCode": 124}
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            env._exec("workstation", ["/usr/bin/ssh", "server"])

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
                     "requires normal-user POSIX file permissions")
    def test_unreadable_learner_file_is_objective_state_not_probe_failure(self):
        env = self.make_env()
        def local_probe(service, code, data, user="1000:1000"):
            result = subprocess.run([sys.executable, "-I", "-c", code, json.dumps(data)],
                                    capture_output=True, text=True, check=True, timeout=5)
            return json.loads(result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private"
            path.write_text("unchanged", encoding="utf-8")
            path.chmod(0)
            try:
                with mock.patch.object(env, "_python", side_effect=local_probe):
                    value = env.snapshot("workstation", [str(path)])[str(path)]
                    self.assertEqual(value["mode"], 0)
                    self.assertIsNone(value["text"])
            finally:
                path.chmod(0o600)


if __name__ == "__main__":
    unittest.main()
