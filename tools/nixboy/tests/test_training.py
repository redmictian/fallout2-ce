import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import protocol as wire
from training import AttemptManager, TrainingSession
from challenge_content import Catalog
from test_bridge import FakeClient, TOKEN, eventually


def context(claimed=0, receipt=None, unarmed=None, science=10, receipt_level=1):
    result = {"training.version": 2, "training.profile.0": -1,
              "training.profile.1": 2, "training.profile.2": 3, "training.profile.3": 4,
              "training.claimed.unarmed": claimed, "training.claimed.science": 0,
              "skill.unarmed": 15 + claimed.bit_count() * 5 if unarmed is None else unarmed,
              "skill.science": science}
    if receipt:
        for index, word in enumerate(struct.unpack("!4i", receipt)):
            result[f"training.receipt.unarmed.{receipt_level}.{index}"] = word
    return result


class FakeCatalog(Catalog):
    def __init__(self):
        self.tracks = Catalog().tracks

    def pool(self, skill, level):
        return [f"{skill}-{level}-{index}" for index in range(5)]

    def get(self, identifier):
        skill, level, option = map(int, identifier.split("-"))
        return types.SimpleNamespace(id=identifier, skill=skill, level=level,
                                     title=f"Exercise {identifier}",
                                     instructions="Create {name}.")

    def instantiate(self, identifier, seed):
        return {"name": f"sample-{seed % 100}"}


class FakeEnvironment:
    def __init__(self, client, spec, attempt_id, seed, generation, state_dir, params=None):
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
        profile, completed = wire.training_context(context())
        self.assertEqual(profile, "ffffffff000000020000000300000004")
        self.assertEqual(completed, {3: 0, 12: 0})
        receipt = bytes(range(1, 17))
        encoded = wire.encode_training_reward(3, 1, receipt)
        self.assertEqual(len(encoded), 18)
        self.assertEqual(wire.decode_training_ack(encoded + b"\1"), (3, 1, receipt.hex(), True))
        for invalid in (b"", encoded, encoded + b"\2", b"\3\1" + bytes(16) + b"\1"):
            with self.assertRaises(wire.ProtocolError):
                wire.decode_training_ack(invalid)

    def test_invalid_handshakes(self):
        for key, value in (("training.version", 1), ("training.claimed.science", 4),
                           ("training.profile.0", None), ("skill.science", None)):
            values = context()
            values[key] = value
            with self.assertRaises(wire.ProtocolError):
                wire.training_context(values)


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environments = []
        self.manager = self.make_manager()
        self.profile, self.completed = wire.training_context(context())
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
        self.assertEqual(data["profiles"][self.profile]["receipts"]["3:1"], pending[0][2])
        self.manager.acknowledge(self.profile, *pending[0], True)
        self.manager.abandon(self.profile)
        self.manager.close()
        self.manager = self.make_manager()
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), pending)
        self.assertEqual(self.manager.pending_rewards(self.profile, {3: 1, 12: 0}), [])

    def test_practice_success_never_rewards(self):
        self.manager.select(self.profile, 12, 1, "practice")
        self.manager.prepare(self.profile)
        result = self.manager.check(self.profile)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["rewarded"])
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [])

    def test_game_snapshot_recovers_exact_receipt_without_fabricated_pass(self):
        receipt = bytes(range(16))
        self.manager.observe(self.profile, {3: 1, 12: 0}, context(1, receipt))
        state = self.manager.snapshot(self.profile)
        self.assertEqual(state["receipts"]["3:1"], receipt.hex())
        self.assertIn("3:1", state["recovered_from_game"])
        self.assertNotIn("passes", state)
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [(3, 1, receipt.hex())])
        with self.assertRaises(wire.ProtocolError):
            self.manager.observe(self.profile, {3: 1, 12: 0}, context(1, bytes([99]) * 16))

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
        self.assertEqual(state["claimed"]["3"], 2)
        self.assertNotIn("3:1", state["receipts"])
        reward = self.manager.pending_rewards(self.profile, self.completed)
        self.assertEqual([(skill, level) for skill, level, _ in reward], [(3, 2)])
        self.assertEqual(self.manager.pending_rewards(self.profile, {3: 2, 12: 0}), [])
        with self.assertRaises(ValueError):
            self.manager.select(self.profile, 3, 2)
        self.manager.select(self.profile, 3, 2, "practice")

    def test_sparse_game_receipt_recovery_imports_only_its_claimed_slot(self):
        receipt = bytes(range(1, 17))
        self.manager.observe(self.profile, {3: 2, 12: 0}, context(2, receipt, receipt_level=2))
        state = self.manager.snapshot(self.profile)
        self.assertEqual(state["claimed"]["3"], 2)
        self.assertEqual(state["receipts"], {"3:2": receipt.hex()})
        self.assertEqual(self.manager.pending_rewards(self.profile, self.completed), [(3, 2, receipt.hex())])

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

    def test_version_one_migration_preserves_queue_attempt_and_exact_receipts(self):
        attempt = self.manager.select(self.profile, 3, 1)
        self.manager.close()
        data = json.loads(self.manager.path.read_text())
        profile = data["profiles"][self.profile]
        queue_before = list(profile["queues"]["3:1"])
        data["version"] = 1
        profile.pop("claimed")
        profile["earned"] = {"3": 0, "12": 2}
        profile["receipts"] = {"12:1": "01" * 16, "12:2": "02" * 16}
        self.manager.path.write_text(json.dumps(data), encoding="utf-8")
        self.manager = self.make_manager()
        restored = self.manager.snapshot(self.profile)
        self.assertEqual(restored["claimed"], {"3": 0, "12": 3})
        self.assertEqual(restored["queues"]["3:1"], queue_before)
        self.assertEqual(restored["attempt"]["id"], attempt["id"])
        self.assertEqual(restored["attempt"]["status"], "needs_reset")
        self.assertEqual(restored["receipts"], profile["receipts"])
        self.assertEqual(json.loads(self.manager.path.read_text())["version"], 2)
        self.assertNotIn("earned", restored)

    def test_shutdown_recreates_same_attempt_after_restart(self):
        self.manager.observe(self.profile, self.completed, context(science=15))
        attempt = self.manager.select(self.profile, 12, 2, "practice")
        self.manager.prepare(self.profile)
        self.manager.close()
        self.assertTrue(self.environments[-1].closed)
        self.manager = self.make_manager()
        self.assertEqual(self.manager.snapshot(self.profile)["attempt"]["status"], "needs_reset")
        self.manager.prepare(self.profile)
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

    def connect(self, values=None):
        sock = socket.create_connection(self.server.server_address, timeout=3)
        sock.settimeout(3)
        self.sockets.append(sock)
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(
            wire.TRAIN_OPEN, struct.pack("!HH", 35, 139) + wire.encode_context(values or context())))
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
        values = context(1, reward[2:])
        restored.sendall(wire.frame(wire.CONTEXT, wire.encode_context(values)))
        output = self.until_output(restored, b"completed 1/2")
        self.assertIn(b"unarmed", output)
        self.assertEqual(self.manager.pending_rewards(wire.training_context(context())[0], {3: 1, 12: 0}), [])

    def test_practice_check_has_no_reward(self):
        sock = self.connect()
        self.start(sock, "p")
        sock.sendall(wire.frame(wire.TRAIN_ACTION, bytes([wire.TRAIN_CHECK])))
        self.until_output(sock, b"Practice completed; no reward")
        profile = wire.training_context(context())[0]
        self.assertEqual(self.manager.pending_rewards(profile, {3: 0, 12: 0}), [])

    def test_shell_exit_returns_hub_without_completion(self):
        sock = self.connect()
        self.start(sock)
        tty_client = next(client for client in self.clients if hasattr(client, "tty_peer"))
        tty_client.tty_peer.shutdown(socket.SHUT_WR)
        self.until_output(sock, b"Shell closed. Exercise state remains available")
        profile = wire.training_context(context())[0]
        self.assertIsNotNone(self.manager.snapshot(profile)["attempt"])
        self.assertFalse(self.environments[-1].closed)
        self.assertEqual(self.manager.pending_rewards(profile, {3: 0, 12: 0}), [])

    def test_bad_training_context_never_contacts_docker(self):
        sock = socket.create_connection(self.server.server_address, timeout=3)
        self.sockets.append(sock)
        invalid = context()
        invalid["training.version"] = 99
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.TRAIN_OPEN,
                     struct.pack("!HH", 35, 139) + wire.encode_context(invalid)))
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

    def test_missing_recovery_receipt_returns_useful_error_before_docker(self):
        sock = socket.create_connection(self.server.server_address, timeout=3)
        self.sockets.append(sock)
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.TRAIN_OPEN,
                     struct.pack("!HH", 35, 139) + wire.encode_context(context(1))))
        kind, message = wire.read_frame(sock)
        self.assertEqual(kind, wire.ERROR)
        self.assertIn(b"recovery failed", message)
        self.assertIn(b"restore training state", message)
        self.assertEqual(self.clients, [])


if __name__ == "__main__":
    unittest.main()
