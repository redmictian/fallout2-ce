import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import socket
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protocol as wire
from challenge_content import Catalog
from education_records import export_snapshot, read_snapshot, restore_snapshot
from progression_state import ZERO_SNAPSHOT
from training import AttemptManager, TrainingSession
from test_bridge import FakeClient
from test_course_progress import CourseEnvironment
from test_training import context
from bridge import BridgeServer


class SaveBoundProgressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.catalog = Catalog(Path(__file__).resolve().parents[1]/"content-assessment-test")
        self.environments = []
        def create(*args, **kwargs):
            result = CourseEnvironment(*args, **kwargs)
            self.environments.append(result)
            return result
        self.manager = AttemptManager(self.directory.name, FakeClient, self.catalog, create)
        self.addCleanup(self.manager.close)
        self.values = {**context(), "training.version": 6, "perk.toughness": 1,
                       "perk.toughness.max": 4, "perk.training.enabled": 1}
        self.identity, _ = wire.training_context(self.values)
        self.epoch = "11"*16
        self.activate(self.epoch, ZERO_SNAPSHOT)

    def activate(self, epoch, snapshot, claims=None, perks=None):
        result = self.manager.activate_progression(self.identity, epoch, snapshot, True,
            claims or {}, perks or {}, self.values)
        self.epoch = epoch
        return result

    def capture(self, claims=None, perks=None):
        return self.manager.capture_progression(self.identity, self.epoch, claims or {}, perks or {}, self.values)

    def complete_intro(self):
        self.manager.select_course(self.identity, "test.intro")
        attempt = self.manager.snapshot(self.identity)["attempt"]
        alt = self.catalog.course.alternative("test.intro", attempt["alternative"])
        for question in alt.questions:
            self.manager.draft_answer(self.identity, question.id, question.answers[0])
        self.manager.commit_answers(self.identity)
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")

    def statuses(self):
        return {item["id"]: item for item in self.manager.course_status(self.identity)}

    def test_a_b_select_exact_progress_queues_receipts_and_reearning(self):
        a = self.capture()
        self.complete_intro()
        self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        self.assertTrue(self.manager.check(self.identity)["rewarded"])
        earned = self.manager.snapshot(self.identity)
        claims = copy.deepcopy(earned["receipts"])
        b = self.capture(claims)
        self.assertTrue(self.statuses()["test.mixed"]["eligible"])
        self.activate("22"*16, a)
        rolled = self.manager.snapshot(self.identity)
        self.assertFalse(self.statuses()["test.mixed"]["eligible"])
        self.assertFalse(rolled["receipts"])
        self.assertEqual(rolled["queues"], {})
        self.assertFalse(rolled.get("education", {}).get("seen"))
        self.complete_intro()
        self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        self.assertTrue(self.manager.check(self.identity)["rewarded"])
        again = self.manager.snapshot(self.identity)["receipts"]
        self.assertNotEqual(again["12:61000"]["receipt"], claims["12:61000"]["receipt"])
        self.activate("33"*16, b, claims)
        restored = self.manager.snapshot(self.identity)
        self.assertEqual(restored["receipts"], claims)
        self.assertEqual(restored["queues"], earned["queues"])
        self.assertEqual(restored["education"], earned["education"])
        self.assertEqual(self.manager.pending_rewards(self.identity, claims), [])

    def test_reopen_same_activation_preserves_unsaved_draft_and_pending_reward(self):
        a = self.capture()
        self.manager.select_course(self.identity, "test.intro")
        self.manager.draft_answer(self.identity, "q1", "1")
        before = self.manager.snapshot(self.identity)
        self.assertFalse(self.activate(self.epoch, a)["restored"])
        self.assertEqual({k:v for k,v in self.manager.snapshot(self.identity).items() if k != "updated"},
                         {k:v for k,v in before.items() if k != "updated"})
        self.manager.abandon(self.identity)
        self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        self.manager.check(self.identity)
        pending = self.manager.pending_rewards(self.identity, {})
        self.activate(self.epoch, a)
        self.assertEqual(self.manager.pending_rewards(self.identity, {}), pending)

    def test_restore_practical_discards_runtime_and_evidence_preserves_saved_k(self):
        self.complete_intro()
        self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        attempt = self.manager.snapshot(self.identity)["attempt"]
        alt = self.catalog.course.alternative("test.mixed", attempt["alternative"])
        for question in alt.questions:
            self.manager.draft_answer(self.identity, question.id, question.answers[0])
        self.manager.commit_answers(self.identity)
        a = self.capture()
        old = self.manager.snapshot(self.identity)
        self.activate("22"*16, a)
        restored = self.manager.snapshot(self.identity)
        self.assertTrue(self.environments[-1].closed)
        self.assertFalse(self.manager.environments)
        self.assertNotEqual(restored["attempt"]["id"], old["attempt"]["id"])
        self.assertEqual(restored["attempt"]["answers"], old["attempt"]["answers"])
        self.assertTrue(restored["attempt"]["conceptual"]["passed"])
        self.assertIsNone(restored["attempt"]["practical"])
        self.assertEqual(restored["attempt"]["checkpoints"], [])
        self.assertEqual(restored["education"], old["education"])
        self.manager.prepare(self.identity)
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")

    def test_missing_corrupt_cross_profile_or_wrong_claim_snapshot_never_imports_latest(self):
        a = self.capture()
        self.complete_intro()
        before = self.manager.snapshot(self.identity)
        for snapshot in ("ff"*32, a):
            path = Path(self.directory.name)/"progression-snapshots"/(snapshot+".json")
            if snapshot == a:
                path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(wire.ProtocolError, "missing or corrupt"):
                self.activate("22"*16, snapshot)
            self.assertEqual({k:v for k,v in self.manager.snapshot(self.identity).items() if k != "updated"},
                         {k:v for k,v in before.items() if k != "updated"})
            self.manager.assert_activation(self.identity, "11"*16)

    def test_saved_undelivered_receipt_is_pending_only_in_that_save(self):
        a = self.capture()
        self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        self.manager.check(self.identity)
        pending = self.manager.pending_rewards(self.identity, {})
        b = self.capture()  # No game claim yet; the earned receipt is saved pending.
        self.activate("22"*16, a)
        self.assertFalse(self.manager.pending_rewards(self.identity, {}))
        self.activate("33"*16, b)
        self.assertEqual(self.manager.pending_rewards(self.identity, {}), pending)

    def test_retired_epoch_and_old_ack_or_worker_session_cannot_return(self):
        a = self.capture()
        session = TrainingSession(self.manager, lambda *args: None, mock.Mock())
        binding = {"activation": self.epoch, "snapshot": a, "restore": True}
        session.start(35, 139, self.values, {}, {}, binding=binding)
        self.addCleanup(session.close)
        self.activate("22"*16, a)
        with self.assertRaisesRegex(wire.ProtocolError, "Discarded"):
            self.activate("11"*16, a)
        for work in (session.rewards, session.poll, lambda: session.input(b"C\n"),
                     lambda: session.acknowledge(12, 61000, 1, "44"*16, True)):
            with self.assertRaises(wire.ProtocolError):
                work()
        with self.assertRaisesRegex(wire.ProtocolError, "save-bound"):
            self.manager.observe(self.identity, {}, {**self.values, "training.version": 5})

    def test_legacy_baseline_imports_exact_saved_claims_without_later_history(self):
        self.complete_intro()
        claims = {"12:61000": {"receipt": "44"*16, "points": 1}}
        perks = {"12:2": "55"*16}
        notice = self.activate("22"*16, ZERO_SNAPSHOT, claims, perks)["notice"]
        state = self.manager.snapshot(self.identity)
        self.assertIn("no educational snapshot", notice)
        self.assertEqual(state["receipts"], claims)
        self.assertEqual(state["perk_receipts"]["12:2"]["receipt"], perks["12:2"])
        self.assertFalse(self.statuses()["test.intro"]["complete"])

    def test_backup_includes_immutable_save_associations(self):
        a = self.capture()
        output = Path(self.directory.name)/"backup.json"
        result = export_snapshot(self.directory.name, output)
        self.assertEqual(result["progression_snapshots"], 1)
        self.assertEqual(read_snapshot(output)["version"], 5)
        self.assertTrue(Path(str(output)+".progression-snapshots", a+".json").is_file())
        target = Path(self.directory.name)/"restored"
        self.assertEqual(restore_snapshot(output, target)["progression_snapshots"], 1)
        self.assertEqual(read_snapshot(target/"training.json")["version"], 5)
        self.assertTrue((target/"progression-snapshots"/(a+".json")).exists())
        with self.assertRaises(FileExistsError):
            restore_snapshot(output, target)
        Path(str(output)+".progression-snapshots", a+".json").unlink()
        with self.assertRaises(OSError):
            read_snapshot(output)

    def test_real_socket_capture_and_load_selector_without_docker(self):
        server = BridgeServer(0, b"a"*64, "unused", training_manager=self.manager)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        worker.start()
        try:
            binding = {"activation": self.epoch, "snapshot": ZERO_SNAPSHOT, "restore": True}
            with socket.create_connection(server.server_address, timeout=3) as client:
                payload = wire.encode_training_open(35, 139, self.values, {}, binding=binding)
                client.sendall(wire.frame(wire.AUTH, b"a"*64)+wire.frame(wire.TRAIN_CAPTURE, payload))
                kind, snapshot = wire.read_frame(client)
                self.assertEqual(kind, wire.TRAIN_SNAPSHOT)
                self.assertEqual(len(snapshot), 32)
            self.complete_intro()
            binding.update(activation="22"*16, snapshot=snapshot.hex())
            with socket.create_connection(server.server_address, timeout=3) as client:
                client.sendall(wire.frame(wire.AUTH, b"a"*64)+wire.frame(wire.TRAIN_OPEN,
                    wire.encode_training_open(35, 139, self.values, {}, binding=binding)))
                self.assertEqual(wire.read_frame(client)[0], wire.READY)
                client.sendall(wire.frame(wire.CLOSE))
            self.assertFalse(self.statuses()["test.intro"]["complete"])
        finally:
            server.shutdown(); server.server_close(); worker.join(2)

    def test_loading_cancels_inflight_validator_before_profile_replacement(self):
        a = self.capture()
        self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        entered, release = threading.Event(), threading.Event()
        def delayed():
            entered.set()
            self.assertTrue(release.wait(3))
            return {"status": "passed"}
        self.environments[-1].check = delayed
        results = []
        checking = threading.Thread(target=lambda: results.append(self.manager.check(self.identity)))
        checking.start(); self.assertTrue(entered.wait(2))
        loading = threading.Thread(target=lambda: self.activate("22"*16, a))
        loading.start()
        # Restore signals cancellation before waiting for the validator's lock.
        self.assertTrue(self.manager.cancel_events[self.identity].wait(2))
        release.set(); checking.join(3); loading.join(3)
        self.assertFalse(checking.is_alive() or loading.is_alive())
        self.assertEqual(results[0]["status"], "error")
        self.assertFalse(self.manager.snapshot(self.identity)["receipts"])
        self.assertFalse(self.manager.environments)

    def test_wrong_save_claims_and_cross_profile_snapshot_are_explicit_errors(self):
        a = self.capture()
        with self.assertRaisesRegex(wire.ProtocolError, "missing or corrupt"):
            self.activate("22"*16, a, {"12:61000": {"receipt": "44"*16, "points": 1}})
        import hashlib
        original = Path(self.directory.name)/"progression-snapshots"/(a+".json")
        data = json.loads(original.read_text()); data["profile"] = "ff"*16
        content = json.dumps(data).encode(); digest = hashlib.sha256(content).hexdigest()
        (original.parent/(digest+".json")).write_bytes(content)
        with self.assertRaisesRegex(wire.ProtocolError, "missing or corrupt"):
            self.activate("22"*16, digest)
        self.manager.assert_activation(self.identity, "11"*16)

    def test_theory_commitment_restores_exact_saved_answers(self):
        self.manager.select_course(self.identity, "test.intro")
        attempt = self.manager.snapshot(self.identity)["attempt"]
        alt = self.catalog.course.alternative("test.intro", attempt["alternative"])
        for question in alt.questions:
            self.manager.draft_answer(self.identity, question.id, question.answers[0])
        feedback = self.manager.commit_answers(self.identity)
        a = self.capture()
        self.manager.check(self.identity)
        self.activate("22"*16, a)
        restored = self.manager.snapshot(self.identity)["attempt"]
        self.assertEqual(restored["id"], attempt["id"])
        self.assertEqual(restored["conceptual"], feedback)
        self.assertFalse(self.statuses()["test.intro"]["complete"])
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")

    def test_project_stage_and_perk_receipt_follow_selected_save(self):
        from test_delivery_progress import NativeFixture, CONTENT
        with tempfile.TemporaryDirectory() as directory:
            manager = AttemptManager(directory, FakeClient, Catalog(CONTENT), NativeFixture)
            try:
                manager.activate_progression(self.identity, "11"*16, ZERO_SNAPSHOT, True, {}, {}, self.values)
                a = manager.capture_progression(self.identity, "11"*16, {}, {}, self.values)
                for stage in (1, 2):
                    manager.select_course(self.identity, f"delivery.nginx.{stage}")
                    manager.prepare(self.identity)
                    manager.checkpoint(self.identity, "reload")
                    if stage == 2:
                        manager.draft_answer(self.identity, "q1", "1"); manager.commit_answers(self.identity)
                    self.assertEqual(manager.check(self.identity)["status"], "passed")
                receipt = manager.snapshot(self.identity)["perk_receipts"]["12:2"]["receipt"]
                b = manager.capture_progression(self.identity, "11"*16, {}, {"12:2": receipt}, self.values)
                manager.activate_progression(self.identity, "22"*16, a, True, {}, {}, self.values)
                self.assertFalse(manager.snapshot(self.identity).get("perk_receipts"))
                self.assertFalse(next(row for row in manager.course_status(self.identity) if row["id"] == "delivery.nginx.2")["eligible"])
                manager.activate_progression(self.identity, "33"*16, b, True, {}, {"12:2": receipt}, self.values)
                self.assertTrue(all(row["complete"] for row in manager.course_status(self.identity)))
                self.assertFalse(manager.pending_perks(self.identity, {"12:2": receipt}))
            finally:
                manager.close()

    def test_loading_another_character_also_removes_previous_owned_runtime(self):
        self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        previous = self.environments[-1]
        other = "ab"*16
        self.manager.activate_progression(other, "22"*16, ZERO_SNAPSHOT, True, {}, {}, self.values)
        self.assertTrue(previous.closed)
        self.assertFalse(self.manager.environments)
        self.assertFalse(self.manager.snapshot(self.identity)["receipts"])
        self.assertFalse(self.manager.snapshot(other)["receipts"])


if __name__ == "__main__":
    unittest.main()
