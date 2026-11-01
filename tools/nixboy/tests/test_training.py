import json
from dataclasses import replace
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import protocol as wire
from training import AttemptManager, TrainingSession, OperationCancelled, IDLE_SECONDS
from challenge_content import Catalog, Challenge
from test_bridge import FakeClient, TOKEN, eventually


def context(unarmed=15, science=10):
    result = {"training.version": 4, "training.profile.0": -1,
              "training.profile.1": 2, "training.profile.2": 3, "training.profile.3": 4}
    result.update({"skill." + name: 25 for name in wire.SKILL_NAMES})
    result.update({"skill.unarmed": unarmed, "skill.science": science})
    return result



def claim(receipt, points=5):
    return {"receipt": receipt.hex() if isinstance(receipt, bytes) else receipt, "points": points}


class FakeCatalog(Catalog):
    def __init__(self):
        actual = Catalog()
        self.tracks, self.root = actual.tracks, actual.root

    def pool(self, skill, level):
        return [f"{skill}-{level}-{index}" for index in range(5)]

    def get(self, identifier):
        skill, level, option = map(int, identifier.split("-"))
        return Challenge(id=identifier, skill=skill, level=level,
                         title=f"Exercise {identifier}", instructions="Create {name}.",
                         kind="copy-file", parameters={"name": ["sample"]})

    def instantiate(self, identifier, seed):
        return {"name": f"sample-{seed % 100}"}


class FakeEnvironment:
    def __init__(self, client, spec, attempt_id, seed, generation, state_dir, params=None, **kwargs):
        self.client, self.spec = client, spec
        self.attempt_id, self.seed, self.generation = attempt_id, seed, generation
        self.params = params
        self.container_id = "fake-nixboy-container"
        self.closed = False
        self.outcome = {"status": "passed", "feedback": "All objectives satisfied."}

    def start(self):
        return self.container_id

    def check(self):
        return self.outcome

    def close(self):
        self.closed = True
        self.client.close()


class TrainingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.stops = 0

    def exec_create(self, container, command, **kwargs):
        if len(command) >= 4 and command[3] == bridge.TRAINING_STOP:
            self.exec_options.append((command, kwargs))
            self.stops += 1
            return {"Id": "stop"}
        return super().exec_create(container, command, **kwargs)

    def exec_start(self, exec_id, **kwargs):
        return b"" if exec_id == "stop" else super().exec_start(exec_id, **kwargs)

    def exec_inspect(self, exec_id):
        return {"Running": False, "ExitCode": 0} if exec_id == "stop" else super().exec_inspect(exec_id)


class TrainingAttachmentTests(unittest.TestCase):
    def test_detach_uses_nonce_owned_container_stop_once(self):
        client = TrainingClient()
        shell = bridge.TrainingShell(client, "owned-exercise", lambda *args: None, lambda: None)
        shell.start(35, 139, {})
        launch = client.exec_options[0]
        self.assertEqual(launch[0][3], bridge.TRAINING_LAUNCH)
        token = launch[0][-1]
        self.assertEqual(launch[1]["environment"]["NIXBOY_ATTACHMENT_TOKEN"], token)
        shell.close()
        shell.close()
        self.assertEqual(client.stops, 1)
        self.assertEqual(client.exec_options[-1][0][-1], token)
        self.assertEqual(client.exec_options[-1][1]["user"], "1000:1000")
        self.assertTrue(client.closed)
        for peer in client.peers:
            peer.close()
        compile(bridge.TRAINING_LAUNCH, "training-launch", "exec")
        compile(bridge.TRAINING_STOP, "training-stop", "exec")


class TrainingProtocolTests(unittest.TestCase):
    def test_profile_words_and_reward_geometry(self):
        profile, skills = wire.training_context(context())
        self.assertEqual(profile, "ffffffff000000020000000300000004")
        self.assertEqual(len(skills), 18)
        self.assertEqual((skills[3], skills[12]), (15, 10))
        receipt = bytes(range(1, 17))
        encoded = wire.encode_training_reward(3, 1, 5, receipt)
        self.assertEqual(len(encoded), 21)
        self.assertEqual(wire.decode_training_ack(encoded + b"\1"), (3, 1, 5, receipt.hex(), True))
        for invalid in (b"", encoded, encoded + b"\2", b"\3\0\1" + bytes(16) + b"\1"):
            with self.assertRaises(wire.ProtocolError):
                wire.decode_training_ack(invalid)

    def test_invalid_handshakes(self):
        for key, value in (("training.version", 2), ("skill.repair", True),
                           ("training.profile.0", None), ("skill.science", None)):
            values = context()
            values[key] = value
            with self.assertRaises(wire.ProtocolError):
                wire.training_context(values)

    def test_sparse_opening_claims_roundtrip_with_uint16_level_and_all_skills(self):
        claims = {"3:2": claim("01" * 16), "13:65535": claim("02" * 16), "17:300": claim("03" * 16)}
        payload = wire.encode_training_open(36, 139, context(), claims)
        self.assertEqual(wire.decode_training_open(payload), (36, 139, context(), claims))
        reward = wire.encode_training_reward(13, 65535, 5, bytes(range(1, 17)))
        self.assertEqual(wire.decode_training_ack(reward + b"\1")[:2], (13, 65535))
        for skill, level in ((18, 1), (-1, 1), (3, 0), (3, 65536)):
            with self.assertRaises(wire.ProtocolError):
                wire.encode_training_reward(skill, level, 5, bytes(range(16)))

    def test_claim_bounds_duplicates_truncation_and_invalid_counts_are_rejected(self):
        claims = {f"0:{level}": claim(level.to_bytes(16, "big")) for level in range(1, 2049)}
        payload = wire.encode_training_open(36, 139, context(), claims)
        self.assertEqual(len(wire.decode_training_open(payload)[3]), 2048)
        claims["0:2049"] = claim("ff" * 16)
        with self.assertRaises(wire.ProtocolError):
            wire.encode_training_open(36, 139, context(), claims)
        for claims in ({"3:1": claim("00" * 16)}, {"3:1": claim("01" * 16), "13:9": claim("01" * 16)}, {"03:1": claim("01" * 16)}):
            with self.assertRaises(wire.ProtocolError):
                wire.encode_training_open(36, 139, context(), claims)
        valid = wire.encode_training_open(36, 139, context(), {"3:1": claim("01" * 16)})
        for invalid in (valid[:-1], valid + b"\0", valid[:4] + struct.pack("!I", 65535) + valid[8:]):
            with self.assertRaises(wire.ProtocolError):
                wire.decode_training_open(invalid)
        # Duplicate raw records cannot be hidden by dictionary construction.
        offset = 8 + struct.unpack_from("!I", valid, 4)[0]
        duplicate = valid[:offset] + b"\0\2" + valid[offset + 2:] * 2
        with self.assertRaises(wire.ProtocolError):
            wire.decode_training_open(duplicate)

    def test_mixed_reward_points_roundtrip_and_invalid_wire_amounts_are_rejected(self):
        claims = {"3:1": claim("01" * 16, 1), "3:2": claim("02" * 16, 7), "13:300": claim("03" * 16, 300)}
        payload = wire.encode_training_open(36, 139, context(), claims)
        self.assertEqual(wire.decode_training_open(payload)[3], claims)
        reward = wire.encode_training_reward(13, 3, 7, bytes(range(16)))
        self.assertEqual(wire.decode_training_ack(reward + b"\1"), (13, 3, 7, bytes(range(16)).hex(), True))
        for invalid in (0, -1, 301, True, 7.0, "7"):
            with self.subTest(points=invalid), self.assertRaises(wire.ProtocolError):
                wire.encode_training_reward(13, 3, invalid, bytes(range(16)))
        invalid_reward = reward[:3] + b"\0\0" + reward[5:]
        with self.assertRaises(wire.ProtocolError):
            wire.decode_training_ack(invalid_reward + b"\1")
        offset = 10 + struct.unpack_from("!I", payload, 4)[0]
        invalid_open = payload[:offset + 3] + b"\0\0" + payload[offset + 5:]
        with self.assertRaises(wire.ProtocolError):
            wire.decode_training_open(invalid_open)


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environments = []
        self.manager = self.make_manager()
        self.profile, _ = wire.training_context(context())
        self.completed = {}
        self.manager.observe(self.profile, self.completed, context())

    def make_manager(self):
        def create(*args, **kwargs):
            environment = FakeEnvironment(*args, **kwargs)
            self.environments.append(environment)
            return environment
        return AttemptManager(self.directory.name, FakeClient, FakeCatalog(), create)

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def test_failure_rotates_once_error_preserves_and_reset_reuses_seed(self):
        attempt = self.manager.select(self.profile, 3, 1)
        queue_before = self.manager.snapshot(self.profile)["queues"]["3:1"]
        self.manager.prepare(self.profile)
        self.environments[-1].outcome = {"status": "error", "feedback": "Daemon unavailable"}
        self.assertEqual(self.manager.check(self.profile)["status"], "error")
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["id"], attempt["id"])
        self.assertEqual(self.manager.snapshot(self.profile)["queues"]["3:1"], queue_before)
        self.manager.reset(self.profile)
        rebuilt = self.manager.snapshot(self.profile)["attempt"]
        self.assertEqual((rebuilt["seed"], rebuilt["params"], rebuilt["id"]),
                         (attempt["seed"], attempt["params"], attempt["id"]))
        self.assertEqual(rebuilt["generation"], 1)
        self.environments[-1].outcome = {"status": "incomplete", "feedback": "File missing"}
        self.assertEqual(self.manager.check(self.profile)["status"], "incomplete")
        after = self.manager.snapshot(self.profile)
        self.assertEqual(after["queues"]["3:1"], queue_before[1:] + queue_before[:1])
        self.assertIsNone(after["attempt"])
        self.assertTrue(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [])

    def test_pass_is_durable_before_delivery_and_survives_abandon_restart(self):
        self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        self.assertTrue(self.manager.check(self.profile)["rewarded"])
        pending = self.manager.pending_rewards(self.profile, self.completed)
        data = json.loads(self.manager.path.read_text())
        self.assertEqual(data["profiles"][self.profile]["pending"]["receipt"], pending[0][3])
        self.manager.acknowledge(self.profile, *pending[0], True)
        self.manager.abandon(self.profile)
        self.manager.close()
        self.manager = self.make_manager()
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [])
        self.assertEqual(self.manager.pending_rewards(self.profile, {"3:1": claim(pending[0][3])}), [])

    def test_practice_success_never_rewards(self):
        self.manager.select(self.profile, 12, 1, "practice")
        self.manager.prepare(self.profile)
        result = self.manager.check(self.profile)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["rewarded"])
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [])


    def test_current_skill_exclusively_selects_its_band_independently_per_track(self):
        for skill, name, cases in ((3, "unarmed", ((14, None), (15, 1), (19, 1), (20, 2), (24, 2), (25, None))),
                                   (12, "science", ((9, None), (10, 1), (14, 1), (15, 2), (19, 2), (20, None)))):
            for value, expected in cases:
                with self.subTest(skill=skill, value=value):
                    values = context()
                    values["skill." + name] = value
                    self.manager.observe(self.profile, self.completed, values)
                    self.assertEqual(self.manager.current_level(self.profile, skill), expected)
                    other = 12 if skill == 3 else 3
                    self.assertEqual(self.manager.current_level(self.profile, other), 1)
                    for level in (1, 2):
                        if level != expected:
                            with self.assertRaises(ValueError):
                                self.manager.select(self.profile, skill, level)
                        else:
                            self.manager.select(self.profile, skill, level)
                            self.manager.abandon(self.profile)

    def test_ordinary_skill_spending_can_skip_level_one_without_fabricating_it(self):
        self.manager.observe(self.profile, self.completed, context(unarmed=20))
        self.manager.select(self.profile, 3, 2)
        self.manager.prepare(self.profile)
        self.assertTrue(self.manager.check(self.profile)["rewarded"])
        state = self.manager.snapshot(self.profile)
        self.assertNotIn("claimed", state)
        self.assertNotIn("receipts", state)
        reward = self.manager.pending_rewards(self.profile, self.completed)
        self.assertEqual([(skill, level) for skill, level, _, _ in reward], [(3, 2)])
        self.assertEqual(self.manager.pending_rewards(self.profile, {"3:2": claim(reward[0][3])}), [])
        with self.assertRaises(ValueError):
            self.manager.select(self.profile, 3, 2)
        self.manager.select(self.profile, 3, 2, "practice")


    def test_changed_skill_band_preserves_attempt_and_refuses_stale_reward(self):
        attempt = self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        before = self.manager.snapshot(self.profile)["queues"]["3:1"]
        self.manager.observe(self.profile, self.completed, context(unarmed=20))
        result = self.manager.check(self.profile)
        self.assertEqual(result["status"], "error")
        self.assertIn("abandon", result["feedback"])
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["id"], attempt["id"])
        self.assertEqual(self.manager.snapshot(self.profile)["queues"]["3:1"], before)
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [])
        self.manager.abandon(self.profile)
        self.manager.select(self.profile, 3, 2)

    def test_skill_change_during_validator_cannot_issue_reward(self):
        self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        def check():
            self.manager.observe(self.profile, self.completed, context(unarmed=20))
            return {"status": "passed", "feedback": "All objectives met"}
        self.environments[-1].check = check
        self.assertEqual(self.manager.check(self.profile)["status"], "error")
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [])
        self.assertIsNotNone(self.manager.snapshot(self.profile)["attempt"])




    def test_generic_repair_level_three_rewards_once_and_survives_reconnect(self):
        self.manager.catalog.tracks[13] = {"id": "processes", "title": "Repair", "knowledge_title": "Processes",
                                           "levels": {3: {"title": "Process reports", "min_skill": 25, "max_skill": 29}}}
        self.manager.select(self.profile, 13, 3)
        self.manager.prepare(self.profile)
        self.assertTrue(self.manager.check(self.profile)["rewarded"])
        pending = self.manager.pending_rewards(self.profile, {})
        self.assertEqual([(skill, level) for skill, level, points, receipt in pending], [(13, 3)])
        receipt = pending[0][3]
        self.manager.acknowledge(self.profile, 13, 3, 5, receipt, True)
        values = context()
        values["skill.repair"] = 30
        self.manager.observe(self.profile, {"13:3": claim(receipt)}, values)
        self.assertEqual(self.manager.pending_rewards(self.profile, {"13:3": claim(receipt)}), [])
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])

    def test_changed_content_blocks_active_attempt_without_losing_progress(self):
        attempt = self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        original = self.manager.catalog.get
        self.manager.catalog.get = lambda identifier: replace(original(identifier), version=2)
        result = self.manager.check(self.profile)
        self.assertEqual(result["status"], "error")
        self.assertIn("content changed; abandon", result["feedback"])
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["id"], attempt["id"])
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
        with self.assertRaisesRegex(ValueError, "content changed; abandon"):
            self.manager.prepare(self.profile)


    def test_reward_edit_during_active_attempt_requires_reselection(self):
        band = self.manager.catalog.tracks[3]["levels"][1]
        band["reward_points"] = 7
        attempt = self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        band["reward_points"] = 8
        checked = self.manager.check(self.profile)
        self.assertEqual(checked["status"], "error")
        self.assertIn("content changed; abandon", checked["feedback"])
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["reward_points"], 7)
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
        self.manager.abandon(self.profile)
        self.assertEqual(self.manager.select(self.profile, 3, 1)["reward_points"], 8)

    def test_tampered_points_cannot_acknowledge_or_replace_an_earned_claim(self):
        self.manager.catalog.tracks[3]["levels"][1]["reward_points"] = 7
        self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        self.manager.check(self.profile)
        receipt = self.manager.pending_rewards(self.profile, {})[0][3]
        for points in (5, 8):
            with self.subTest(points=points):
                with self.assertRaises(wire.ProtocolError):
                    self.manager.acknowledge(self.profile, 3, 1, points, receipt, True)
                with self.assertRaises(wire.ProtocolError):
                    self.manager.observe(self.profile, {"3:1": claim(receipt, points)}, context())
                with self.assertRaises(wire.ProtocolError):
                    self.manager.pending_rewards(self.profile, {"3:1": claim(receipt, points)})
        self.assertEqual(self.manager.snapshot(self.profile)["pending"]["points"], 7)


    def test_shutdown_requires_explicit_facility_reissue_after_restart(self):
        self.manager.observe(self.profile, self.completed, context(science=15))
        attempt = self.manager.select(self.profile, 12, 2, "practice")
        self.manager.prepare(self.profile)
        self.manager.close()
        self.assertTrue(self.environments[-1].closed)
        self.manager = self.make_manager()
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "provider_lost")
        with self.assertRaises(RuntimeError):
            self.manager.prepare(self.profile)
        self.manager.reset(self.profile)
        restored = self.manager.snapshot(self.profile)["attempt"]
        self.assertEqual(restored["id"], attempt["id"])
        self.assertEqual(restored["seed"], attempt["seed"])
        self.assertEqual(restored["generation"], 1)

    def test_close_during_setup_cleans_late_resources_and_preserves_attempt(self):
        self.manager.select(self.profile, 3, 1)
        entered, release = threading.Event(), threading.Event()
        errors = []
        factory = self.manager.environment_factory

        def blocked(*args, **kwargs):
            result = factory(*args, **kwargs)
            original = result.start
            def start():
                entered.set()
                release.wait(2)
                return original()
            result.start = start
            return result

        self.manager.environment_factory = blocked
        def prepare():
            try:
                self.manager.prepare(self.profile)
            except RuntimeError as error:
                errors.append(error)
        worker = threading.Thread(target=prepare)
        worker.start()
        self.assertTrue(entered.wait(2))
        release_timer = threading.Timer(.05, release.set)
        release_timer.start()
        self.manager.close()
        release.set()
        release_timer.join(2)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertTrue(self.environments[-1].closed)
        self.assertFalse(self.manager.environments)
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "needs_reset")

    def test_ordinary_container_survives_ordinary_break_without_expiry(self):
        self.manager.select(self.profile, 3, 1)
        self.manager.prepare(self.profile)
        self.manager.data["profiles"][self.profile]["updated"] = time.time() - IDLE_SECONDS * 2
        self.manager.expire()
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "active")


class GuestCatalog(FakeCatalog):
    def get(self, identifier):
        return replace(super().get(identifier), kind="qemu-bt05")

    def digest(self, identifier):
        return "unit-guest-artifacts-v1"


class FakeGuestEnvironment(FakeEnvironment):
    persistent = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.machine_state = "recovery"
        self.starts = 0

    def start(self, cancel=None, progress=None):
        self.starts += 1
        if progress:
            progress("Runner ready; waiting for genuine guest recovery.")
        if cancel is not None and cancel.is_set():
            raise OperationCancelled("unit preparation cancelled")
        return self.container_id

    def status(self):
        return {"state": self.machine_state}

    def check(self, cancel=None, progress=None):
        return self.outcome

    def attachment(self):
        return {"kind": "qemu-serial", "user": "1001:1001", "argv": ["fixed-console"]}


class GuestAttemptTests(unittest.TestCase):
    """Manager policy boundaries; real guest behavior has a separate live smoke."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environments = []
        self.manager = self.make_manager()
        self.profile, _ = wire.training_context(context())
        self.manager.observe(self.profile, {}, context())

    def make_manager(self):
        def create(*args, **kwargs):
            environment = FakeGuestEnvironment(*args, **kwargs)
            self.environments.append(environment)
            return environment
        catalog = GuestCatalog()
        catalog.tracks[3]["levels"][1]["reward_points"] = 1
        return AttemptManager(self.directory.name, FakeClient, catalog, create)

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def select(self):
        return self.manager.select(self.profile, 3, 1)

    def test_guest_detach_reopen_and_long_break_keep_same_environment(self):
        self.select()
        first = self.manager.prepare(self.profile)
        self.manager.attach_frontend(self.profile)
        self.manager.detach_frontend(self.profile)
        with self.manager.lock:
            self.manager._profile(self.profile)["updated"] = time.time() - IDLE_SECONDS * 2
        self.manager.expire()
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.prepare(self.profile), first)
        self.assertEqual(self.environments[-1].starts, 1)
        self.assertEqual(self.manager.attachment(self.profile)["kind"], "qemu-serial")

    def test_provider_restart_requires_explicit_reissue_and_preserves_queue(self):
        attempt = self.select()
        self.manager.prepare(self.profile)
        before = self.manager.snapshot(self.profile)["queues"]
        self.manager.close()
        self.manager = self.make_manager()
        self.manager.observe(self.profile, {}, context())
        state = self.manager.snapshot(self.profile)
        self.assertEqual(state["attempt"]["status"], "provider_lost")
        with self.assertRaisesRegex(RuntimeError, "provider interrupted"):
            self.manager.prepare(self.profile)
        self.assertEqual(self.manager.snapshot(self.profile)["queues"], before)
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
        self.manager.reset(self.profile)
        reissued = self.manager.snapshot(self.profile)["attempt"]
        self.assertEqual((reissued["id"], reissued["seed"], reissued["generation"]),
                         (attempt["id"], attempt["seed"], 1))
        self.assertNotIn("facility_error", reissued)

    def test_running_machine_with_guest_failure_requires_reset_without_reward(self):
        self.select()
        self.manager.prepare(self.profile)
        self.environments[-1].machine_state = "failure"
        with self.assertRaisesRegex(RuntimeError, "provider interrupted"):
            self.manager.prepare(self.profile)
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "provider_lost")
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
        self.environments[-1].machine_state = "normal"
        with self.assertRaisesRegex(RuntimeError, "provider interrupted"):
            self.manager.prepare(self.profile)

    def test_explicit_facility_failure_is_persistent_until_reset(self):
        self.select()
        self.manager.prepare(self.profile)
        before = self.manager.snapshot(self.profile)["queues"]
        self.environments[-1].outcome = {"status": "error", "state": "failure", "feedback": "Guest kernel panic"}
        self.assertEqual(self.manager.check(self.profile)["status"], "error")
        self.environments[-1].outcome = {"status": "passed", "feedback": "Stale environment must not regain authority"}
        self.assertEqual(self.manager.check(self.profile)["status"], "error")
        self.assertEqual(self.manager.snapshot(self.profile)["queues"], before)
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])

    def test_recovery_agent_absence_is_not_learner_failure_and_proof_is_durable(self):
        self.select()
        self.manager.prepare(self.profile)
        before = self.manager.snapshot(self.profile)["queues"]
        self.environments[-1].outcome = {"status": "error", "feedback": "Expected recovery: guest agent unavailable; continue using serial."}
        self.assertEqual(self.manager.check(self.profile)["status"], "error")
        self.assertEqual(self.manager.snapshot(self.profile)["queues"], before)
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
        proof = {"initial_boot_id": "old", "boot_id": "new", "guest_reset": True,
                 "correct_uuid": True, "outside_http_status": 200}
        self.environments[-1].outcome = {"status": "passed", "feedback": "real probes passed", "evidence": proof}
        self.assertEqual(self.manager.check(self.profile)["reward_points"], 1)
        pending = self.manager.pending_rewards(self.profile, {})
        self.assertEqual(pending[0][:3], (3, 1, 1))
        saved = json.loads(self.manager.path.read_text())["profiles"][self.profile]
        self.assertNotIn("passes", saved)
        self.assertEqual(saved["pending"]["kind"], "skill")
        self.manager.acknowledge(self.profile, *pending[0], True)
        # Once accepted, a completion is not replayed from external history.
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
        self.assertEqual(self.manager.pending_rewards(self.profile, {"3:1": claim(pending[0][3], 1)}), [])
        self.assertFalse(self.manager.check(self.profile).get("rewarded", False))

    def test_invalid_or_oversize_proof_cannot_leave_a_pending_reward(self):
        self.select()
        self.manager.prepare(self.profile)
        for proof in ({"bad": object()}, {"huge": "x" * 65537}):
            self.environments[-1].outcome = {"status": "passed", "feedback": "unusable proof", "evidence": proof}
            self.assertEqual(self.manager.check(self.profile)["status"], "error")
            self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])
            self.assertIsNotNone(self.manager.snapshot(self.profile)["attempt"])

    def test_cancelled_preparation_cleans_partial_environment(self):
        self.select()
        entered, cancelled = threading.Event(), threading.Event()
        original = self.manager.environment_factory
        def create(*args, **kwargs):
            environment = original(*args, **kwargs)
            def start(cancel=None, progress=None):
                entered.set()
                if not cancel.wait(2):
                    raise RuntimeError("unit cancellation was not delivered")
                cancelled.set()
                raise OperationCancelled("unit cancelled")
            environment.start = start
            return environment
        self.manager.environment_factory = create
        errors = []
        def prepare():
            try:
                self.manager.prepare(self.profile)
            except OperationCancelled as exc:
                errors.append(exc)
        worker = threading.Thread(target=prepare)
        worker.start()
        self.assertTrue(entered.wait(2))
        self.manager.cancel(self.profile)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(cancelled.is_set() and errors)
        self.assertTrue(self.environments[-1].closed)
        self.assertNotIn(self.profile, self.manager.environments)
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "needs_reset")
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])

    def test_cancelled_check_discards_late_success_without_rotating_or_destroying_guest(self):
        self.select()
        self.manager.prepare(self.profile)
        entered = threading.Event()
        before = self.manager.snapshot(self.profile)["queues"]
        def check(cancel=None, progress=None):
            entered.set()
            cancel.wait(2)
            return {"status": "passed", "feedback": "Late success must be ignored"}
        self.environments[-1].check = check
        results = []
        worker = threading.Thread(target=lambda: results.append(self.manager.check(self.profile)))
        worker.start()
        self.assertTrue(entered.wait(2))
        self.manager.cancel(self.profile)
        worker.join(3)
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(self.manager.snapshot(self.profile)["queues"], before)
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(self.profile, {}), [])

    def test_progress_does_not_clear_busy_job_and_f8_cancels_then_abandons(self):
        self.select()
        original = self.manager.environment_factory
        entered = threading.Event()
        def create(*args, **kwargs):
            environment = original(*args, **kwargs)
            def start(cancel=None, progress=None):
                progress("Guest kernel booting; serial recovery is next.")
                entered.set()
                cancel.wait(2)
                raise OperationCancelled("unit cancelled")
            environment.start = start
            return environment
        self.manager.environment_factory = create
        output = []
        session = TrainingSession(self.manager, lambda kind, payload=b"": output.append((kind, payload)), None)
        try:
            session.start(36, 139, context(), {})
            session._launch(self.manager.prepare)
            self.assertTrue(entered.wait(2))
            session.poll()
            self.assertTrue(session.job and session.job.is_alive())
            self.assertIn(b"Guest kernel booting", b"".join(x[1] for x in output))
            session.action(wire.TRAIN_ABANDON)
            eventually(lambda: not session.job.is_alive())
            session.poll()
            eventually(lambda: session.job is not None and not session.job.is_alive())
            session.poll()
            self.assertIsNone(self.manager.snapshot(self.profile)["attempt"])
            self.assertTrue(self.environments[-1].closed)
        finally:
            session.close()

    def test_f10_during_preparation_cancels_but_retains_seed_for_explicit_resume(self):
        self.select()
        original = self.manager.environment_factory
        entered = threading.Event()
        def create(*args, **kwargs):
            environment = original(*args, **kwargs)
            def start(cancel=None, progress=None):
                entered.set()
                cancel.wait(2)
                raise OperationCancelled("unit closed during preparation")
            environment.start = start
            return environment
        self.manager.environment_factory = create
        session = TrainingSession(self.manager, lambda *args: None, None)
        session.start(36, 139, context(), {})
        session._launch(self.manager.prepare)
        self.assertTrue(entered.wait(2))
        worker = session.job
        session.close()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.environments[-1].closed)
        self.assertIsNotNone(self.manager.snapshot(self.profile)["attempt"])
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "needs_reset")

    def test_f10_after_attachment_only_detaches_serial_and_same_guest_reopens(self):
        self.select()
        shells, attachments = [], []
        class Shell:
            def __init__(self):
                self.closed = False
            def start(self, *args):
                pass
            def close(self):
                self.closed = True
        def factory(container, send, finished, attachment=None):
            attachments.append(attachment)
            shell = Shell()
            shells.append(shell)
            return shell
        for _ in range(2):
            session = TrainingSession(self.manager, lambda *args: None, factory)
            session.start(36, 139, context(), {})
            session._launch(self.manager.prepare)
            session.job.join(3)
            self.assertFalse(session.job.is_alive())
            session.poll()
            session.close()
        self.assertTrue(all(shell.closed for shell in shells))
        self.assertEqual(len(self.environments), 1)
        self.assertFalse(self.environments[0].closed)
        self.assertEqual(self.environments[0].starts, 1)
        self.assertTrue(all(item["kind"] == "qemu-serial" for item in attachments))

    def test_expected_recovery_is_displayed_as_recovery_not_facility_failure(self):
        self.select()
        self.manager.prepare(self.profile)
        self.environments[-1].outcome = {"status": "error", "facility_state": "recovery",
            "feedback": "The serial recovery console remains available."}
        output = []
        session = TrainingSession(self.manager, lambda kind, payload=b"": output.append(payload), None)
        try:
            session.start(36, 139, context(), {})
            session.action(wire.TRAIN_CHECK)
            session.job.join(3)
            session.poll()
            self.assertIn(b"Expected recovery", output[-1])
            self.assertNotIn(b"Environment/validator error", output[-1])
            self.assertIsNotNone(self.manager.snapshot(self.profile)["attempt"])
        finally:
            session.close()


class TrainingMenuTests(unittest.TestCase):
    def test_menu_and_task_context_display_the_configured_reward(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = FakeCatalog()
            catalog.tracks[3]["levels"][1]["reward_points"] = 7
            manager = AttemptManager(directory, FakeClient, catalog, FakeEnvironment)
            output = []
            session = TrainingSession(manager, lambda kind, payload=b"": output.append((kind, payload)), None)
            try:
                session.start(36, 139, context(), {})
                self.assertIn(b"[+7]", output[-1][1])
                session.track_menu(3)
                self.assertIn(b"earn +7 skill", output[-1][1])
                attempt = manager.select(session.identity, 3, 1)
                self.assertIn("assessment reward +7", manager._training_label(attempt))
            finally:
                session.close()
                manager.close()

    def test_all_authored_skill_tracks_paginate_and_select_by_dynamic_number(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = FakeCatalog()
            catalog.tracks = {skill: {"id": name, "title": name.title(), "knowledge_title": "Processes",
                                      "levels": {3: {"title": "Process reports", "min_skill": 25, "max_skill": 29}}}
                              for skill, name in enumerate(wire.SKILL_NAMES)}
            manager = AttemptManager(directory, FakeClient, catalog, FakeEnvironment)
            output = []
            session = TrainingSession(manager, lambda kind, payload=b"": output.append((kind, payload)), None)
            values = context(unarmed=25, science=25)
            try:
                session.start(36, 139, values, {})
                first = output[-1][1].decode()
                self.assertLessEqual(first.count("\n"), 35)
                self.assertIn("Page 1/2", first)
                self.assertIn("Processes", first)
                session.input(b"n\r")
                second = output[-1][1].decode()
                self.assertIn("Page 2/2", second)
                self.assertIn("[18] Outdoorsman", second)
                session.input(b"18\r")
                self.assertEqual(session.selected, (17, 3))
                self.assertIn(b"Processes / Process reports", output[-1][1])
            finally:
                session.close()
                manager.close()

    def test_plain_context_updates_keep_initial_receipts_and_removed_content_can_be_abandoned(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = AttemptManager(directory, FakeClient, FakeCatalog(), FakeEnvironment)
            output = []
            session = TrainingSession(manager, lambda kind, payload=b"": output.append((kind, payload)), None)
            try:
                session.start(36, 139, context(), {"13:65535": claim("01" * 16)})
                session.context(context(unarmed=20))
                self.assertEqual(session.claimed, {"13:65535": claim("01" * 16)})
                manager.select(session.identity, 3, 2)
                manager.catalog.tracks.pop(3)
                session.hub()
                self.assertIn(b"[R] Resume unarmed level 2", output[-1][1])
                manager.abandon(session.identity)
                self.assertIsNone(manager.snapshot(session.identity)["attempt"])
                self.assertNotIn("receipts", manager.snapshot(session.identity))
                self.assertEqual(manager.game_claims[session.identity], {"13:65535": claim("01" * 16)})
            finally:
                session.close()
                manager.close()


class TrainingSocketTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environments, self.clients, self.sockets = [], [], []

        def client():
            result = TrainingClient()
            self.clients.append(result)
            return result

        def environment(*args, **kwargs):
            result = FakeEnvironment(*args, **kwargs)
            self.environments.append(result)
            return result

        self.manager = AttemptManager(self.directory.name, client, FakeCatalog(), environment)
        self.server = bridge.BridgeServer(0, TOKEN, "legacy:fixed", client_factory=client,
                                          training_manager=self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        for sock in self.sockets:
            sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        for client in self.clients:
            for peer in client.peers:
                peer.close()
        self.directory.cleanup()

    def connect(self, values=None, progress=None):
        sock = socket.create_connection(self.server.server_address, timeout=3)
        sock.settimeout(3)
        self.sockets.append(sock)
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(
            wire.TRAIN_OPEN, wire.encode_training_open(35, 139, values or context(), progress or {})))
        self.assertEqual(wire.read_frame(sock), (wire.READY, b""))
        self.until_output(sock, b"Choose a number")
        return sock

    def until_output(self, sock, expected):
        output = bytearray()
        while expected not in output:
            kind, data = wire.read_frame(sock)
            if kind != wire.OUTPUT:
                self.fail(f"expected menu output, got type {kind}")
            output.extend(data)
        return bytes(output)

    def start(self, sock, mode="a"):
        sock.sendall(wire.frame(wire.INPUT, b"1\r" + mode.encode() + b"\r"))
        self.until_output(sock, b"F5 Check | F6 Hub | F7 Reset | F8 Abandon")
        eventually(lambda: any(hasattr(client, "tty_peer") for client in self.clients))
        eventually(lambda: any(getattr(session, "job", None) is None
                               for session in self.server.sessions))

    def test_menu_shell_input_detach_resume_and_shutdown_cleanup(self):
        sock = self.connect()
        self.assertEqual(self.clients, [])  # A hub is not a privileged container.
        self.start(sock)
        tty_client = next(client for client in self.clients if hasattr(client, "tty_peer"))
        sock.sendall(wire.frame(wire.INPUT, b"echo learning\r"))
        self.assertEqual(wire.recv_exact(tty_client.tty_peer, 14), b"echo learning\r")
        sock.sendall(wire.frame(wire.TRAIN_ACTION, bytes([wire.TRAIN_HUB])))
        self.until_output(sock, b"Choose a number")
        self.assertFalse(self.environments[-1].closed)
        sock.sendall(wire.frame(wire.CLOSE))
        eventually(lambda: not self.server.sessions)
        self.assertFalse(self.environments[-1].closed)
        resumed = self.connect()
        resumed.sendall(wire.frame(wire.INPUT, b"r\r"))
        self.until_output(resumed, b"F5 Check")
        self.assertEqual(len(self.environments), 1)

    def test_validation_error_keeps_attempt_incomplete_rotates(self):
        sock = self.connect()
        self.start(sock)
        profile = wire.training_context(context())[0]
        before = self.manager.snapshot(profile)
        self.environments[-1].outcome = {"status": "error", "feedback": "Probe unavailable"}
        sock.sendall(wire.frame(wire.TRAIN_ACTION, bytes([wire.TRAIN_CHECK])))
        self.until_output(sock, b"Probe unavailable")
        self.assertEqual(self.manager.snapshot(profile)["attempt"]["id"], before["attempt"]["id"])
        self.environments[-1].outcome = {"status": "incomplete", "feedback": "Missing objective"}
        sock.sendall(wire.frame(wire.TRAIN_ACTION, bytes([wire.TRAIN_CHECK])))
        self.until_output(sock, b"Missing objective")
        after = self.manager.snapshot(profile)
        self.assertEqual(after["queues"]["3:1"], before["queues"]["3:1"][1:] + before["queues"]["3:1"][:1])
        self.assertIsNone(after["attempt"])

    def test_pass_redelivers_after_lost_ack_and_ack_is_idempotent(self):
        sock = self.connect()
        self.start(sock)
        sock.sendall(wire.frame(wire.TRAIN_ACTION, bytes([wire.TRAIN_CHECK])))
        self.until_output(sock, b"Skill reward earned")
        kind, reward = wire.read_frame(sock)
        self.assertEqual(kind, wire.TRAIN_REWARD)
        sock.close()
        eventually(lambda: not self.server.sessions)
        restored = self.connect()
        self.assertEqual(wire.read_frame(restored), (wire.TRAIN_REWARD, reward))
        restored.sendall(wire.frame(wire.TRAIN_ACK, reward + b"\1") * 2)
        values = context(unarmed=20)
        restored.sendall(wire.frame(wire.CONTEXT, wire.encode_context(values)))
        output = self.until_output(restored, b"completed 1/2")
        self.assertIn(b"Unarmed", output)
        self.assertEqual(self.manager.pending_rewards(wire.training_context(context())[0], {"3:1": claim(reward[5:].hex())}), [])

    def test_practice_check_has_no_reward(self):
        sock = self.connect()
        self.start(sock, "p")
        sock.sendall(wire.frame(wire.TRAIN_ACTION, bytes([wire.TRAIN_CHECK])))
        self.until_output(sock, b"Practice completed; no reward")
        profile = wire.training_context(context())[0]
        self.assertEqual(self.manager.pending_rewards(profile, {}), [])

    def test_shell_exit_returns_hub_without_completion(self):
        sock = self.connect()
        self.start(sock)
        tty_client = next(client for client in self.clients if hasattr(client, "tty_peer"))
        tty_client.tty_peer.shutdown(socket.SHUT_WR)
        self.until_output(sock, b"Shell closed. Exercise state remains available")
        profile = wire.training_context(context())[0]
        self.assertIsNotNone(self.manager.snapshot(profile)["attempt"])
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(profile, {}), [])

    def test_bad_training_context_never_contacts_docker(self):
        sock = socket.create_connection(self.server.server_address, timeout=3)
        self.sockets.append(sock)
        invalid = wire.encode_training_open(35, 139, context(), {})
        invalid = invalid.replace(b"training.version\x00\x00\x00\x04", b"training.version\x00\x00\x00\x63")
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.TRAIN_OPEN,
                     invalid))
        self.assertEqual(sock.recv(1), b"")
        self.assertEqual(self.clients, [])

    def test_menu_offers_only_practice_assessment_and_hub(self):
        sock = self.connect()
        sock.sendall(wire.frame(wire.INPUT, b"1\r"))
        output = self.until_output(sock, b"Choose a letter")
        self.assertIn(b"Unarmed 15% - level 1 (15-19%)", output)
        self.assertIn(b"[P] Practice", output)
        self.assertIn(b"[A] Assessment", output)
        self.assertNotIn(b"[L]", output)
        self.assertNotIn(b"Lesson", output)
        self.assertEqual(self.clients, [])

    def test_zero_recovery_receipt_is_rejected_before_docker(self):
        sock = socket.create_connection(self.server.server_address, timeout=3)
        self.sockets.append(sock)
        payload = wire.encode_training_open(35, 139, context(), {"3:1": claim("01" * 16)})
        payload = payload[:-16] + bytes(16)
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.TRAIN_OPEN, payload))
        self.assertEqual(sock.recv(1), b"")
        self.assertEqual(self.clients, [])


if __name__ == "__main__":
    unittest.main()
