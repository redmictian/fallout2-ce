"""Socket-level checks for the opt-in acceptance relay, not a reward authority."""
from pathlib import Path
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol as wire
from reward_duplicate_proxy import ReceiptRelay
from test_perk_protocol import context


class RewardRelayTests(unittest.TestCase):
    def forward_pair(self, relay, to_game):
        source, source_peer = socket.socketpair()
        target, target_peer = socket.socketpair()
        target_peer.settimeout(1)
        def run():
            try:
                relay.forward(source, target, to_game)
            except (EOFError, OSError):
                pass
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        def cleanup():
            for sock in (source_peer, target_peer, source, target):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            worker.join(1)
        self.addCleanup(cleanup)
        return source_peer, target_peer

    def test_only_received_genuine_frame_is_duplicated_once(self):
        events = []
        relay = ReceiptRelay(events.append, "perk")
        upstream, game = self.forward_pair(relay, True)
        payload = wire.encode_training_perk_reward("11" * 16, 12, 2, b"\x22" * 16)
        game.settimeout(.02)
        with self.assertRaises(socket.timeout):
            wire.read_frame(game)  # Relay generates nothing before upstream input.
        game.settimeout(1)
        upstream.sendall(wire.frame(wire.TRAIN_PERK_REWARD, payload))
        self.assertEqual(wire.read_frame(game), (wire.TRAIN_PERK_REWARD, payload))
        self.assertEqual(wire.read_frame(game), (wire.TRAIN_PERK_REWARD, payload))
        upstream.sendall(wire.frame(wire.TRAIN_PERK_REWARD, payload))
        self.assertEqual(wire.read_frame(game), (wire.TRAIN_PERK_REWARD, payload))
        self.assertEqual([event["copies"] for event in events], [2, 1])
        self.assertEqual(events[0]["receipt"], "22" * 16)

    def test_drop_perk_ack_preserves_auth_and_context_without_logging_token(self):
        events = []
        relay = ReceiptRelay(events.append, "perk", drop_acks=True)
        game, upstream = self.forward_pair(relay, False)
        token = b"a" * 64
        payload = wire.encode_training_perk_reward("11" * 16, 12, 2, b"\x22" * 16)
        values = context()
        values.update({"perk.toughness": 2, "perk.toughness.max": 4,
                       "perk.training.enabled": 1, "stat.damage_resistance": 10})
        encoded = wire.encode_context(values)
        game.sendall(wire.frame(wire.AUTH, token) + wire.frame(wire.TRAIN_PERK_ACK, payload + b"\x01")
                     + wire.frame(wire.CONTEXT, encoded))
        self.assertEqual(wire.read_frame(upstream), (wire.AUTH, token))
        self.assertEqual(wire.read_frame(upstream), (wire.CONTEXT, encoded))
        self.assertEqual(events[0]["kind"], "game_ack")
        self.assertTrue(events[0]["dropped"])
        self.assertEqual(events[1]["damage_resistance"], 10)
        self.assertNotIn(token.decode(), str(events))

    def test_v4_skill_default_keeps_prior_duplicate_and_ack_behavior(self):
        events = []
        relay = ReceiptRelay(events.append)
        skill = wire.encode_training_reward(12, 60000, 1, b"\x33" * 16)
        self.assertEqual(relay.transfer(wire.TRAIN_REWARD, skill, True), wire.frame(wire.TRAIN_REWARD, skill) * 2)
        self.assertEqual(relay.transfer(wire.TRAIN_ACK, skill + b"\x01", False), wire.frame(wire.TRAIN_ACK, skill + b"\x01"))
        payload = wire.encode_training_open(40, 153, context(4), {})
        self.assertEqual(relay.transfer(wire.TRAIN_OPEN, payload, False), wire.frame(wire.TRAIN_OPEN, payload))
        self.assertEqual(events[-1]["perk_claims"], {})
        self.assertEqual(events[-1]["science"], 10)
        with self.assertRaises(ValueError):
            ReceiptRelay(events.append, "skill", drop_acks=True)

    def test_v6_reward_envelope_is_duplicated_unchanged_and_ack_can_be_lost(self):
        events = []
        relay = ReceiptRelay(events.append, "perk", drop_acks=True)
        activation = "44" * 16
        reward = wire.encode_training_perk_reward("11" * 16, 12, 2, b"\x22" * 16)
        payload = wire.fence_training_payload(reward, activation)
        self.assertEqual(relay.transfer(wire.TRAIN_PERK_REWARD, payload, True),
                         wire.frame(wire.TRAIN_PERK_REWARD, payload) * 2)
        ack = wire.fence_training_payload(reward + b"\1", activation)
        self.assertEqual(relay.transfer(wire.TRAIN_PERK_ACK, ack, False), b"")
        self.assertEqual(events[0]["activation"], activation)
        self.assertEqual(events[0]["receipt"], "22" * 16)
        self.assertTrue(events[1]["accepted"] and events[1]["dropped"])
        self.assertEqual(events[1]["activation"], activation)

    def test_v6_save_capture_and_snapshot_are_observed_without_modification(self):
        events = []
        relay = ReceiptRelay(events.append, "perk")
        values = context(6)
        binding = {"activation": "44" * 16, "snapshot": "55" * 32, "restore": True}
        opening = wire.encode_training_open(40, 153, values, {}, {}, binding=binding)
        self.assertEqual(relay.transfer(wire.TRAIN_CAPTURE, opening, False),
                         wire.frame(wire.TRAIN_CAPTURE, opening))
        snapshot = bytes.fromhex("66" * 32)
        self.assertEqual(relay.transfer(wire.TRAIN_SNAPSHOT, snapshot, True),
                         wire.frame(wire.TRAIN_SNAPSHOT, snapshot))
        self.assertEqual(events[0]["kind"], "game_capture")
        self.assertEqual(events[0]["binding"], binding)
        self.assertEqual(events[1], {"kind": "game_snapshot", "snapshot": "66" * 32})


if __name__ == "__main__":
    unittest.main()
