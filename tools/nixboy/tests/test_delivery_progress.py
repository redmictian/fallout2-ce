"""Project/preparation and typed reward rules; no Docker or game claims."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge_content import Catalog
from nginx_env import preparation_identity
from protocol import ProtocolError, training_context
from training import AttemptManager
from test_bridge import FakeClient
from test_training import FakeEnvironment, context


CONTENT = Path(__file__).resolve().parents[1] / "content-delivery-test"
STAGE_ONE = "delivery.nginx.1"
STAGE_TWO = "delivery.nginx.2"


def delivery_context(**overrides):
    values = context(science=10)
    values.update({"training.version": 5, "perk.training.enabled": 1,
                   "perk.toughness": 1, "perk.toughness.max": 4})
    values.update(overrides)
    return values


class NativeFixture(FakeEnvironment):
    """A fresh canonical object models manager ownership, not nginx behavior."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.preparation_identity = preparation_identity(self.params["preparation"])
        self.preparation = self.params["preparation"]
        self.files = {}
        self.observations = []
        self.outcome = {"status": "passed", "evidence": {"prepared": self.preparation}}

    def start(self, cancel=None, progress=None):
        return self.container_id

    def status(self):
        return {"state": "normal"}

    def checkpoint(self, name):
        self.observations.append(name)
        return {"status": "passed", "evidence": {"observed": name, "preparation": self.preparation}}


class DeliveryProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.catalog = Catalog(CONTENT)
        self.environments = []
        self.clients = mock.Mock(side_effect=FakeClient)
        self.wrong_preparation = False
        self.manager = self.new_manager()
        self.addCleanup(lambda: self.manager.close())
        self.values = delivery_context()
        self.identity, _ = training_context(self.values)
        self.manager.observe(self.identity, {}, self.values)
        self.manager.observe_perks(self.identity, {})

    def factory(self, *args, **kwargs):
        environment = NativeFixture(*args, **kwargs)
        if self.wrong_preparation:
            environment.preparation_identity = "wrong-canonical-preparation"
        self.environments.append(environment)
        return environment

    def new_manager(self):
        return AttemptManager(self.directory.name, self.clients, self.catalog, self.factory)

    def state(self):
        return self.manager.snapshot(self.identity)

    def start(self, gate):
        attempt = self.manager.select_course(self.identity, gate)
        self.manager.prepare(self.identity)
        return attempt

    def finish_one(self):
        self.start(STAGE_ONE)
        self.manager.checkpoint(self.identity, "reload")
        result = self.manager.check(self.identity)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["perk_rewarded"])
        return result

    def answer_two(self):
        self.manager.draft_answer(self.identity, "q1", "1")
        self.assertTrue(self.manager.commit_answers(self.identity)["passed"])

    def finish_two(self):
        self.start(STAGE_TWO)
        self.manager.checkpoint(self.identity, "reload")
        self.answer_two()
        result = self.manager.check(self.identity)
        self.assertEqual(result["status"], "passed")
        return result

    def mutate_catalog(self, mutate):
        root = Path(self.directory.name) / "changed-content"
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(CONTENT, root)
        course = yaml.safe_load((root / "course.yaml").read_text())
        mutate(course)
        (root / "course.yaml").write_text(yaml.safe_dump(course, sort_keys=False))
        return root

    def test_test_only_perk_schema_rejects_other_types_ranks_and_unscoped_mapping(self):
        for change in ({"type": "skill"}, {"perk": 13}, {"target_rank": 3}, {"test_only": False}, {"perk": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Catalog(self.mutate_catalog(lambda data: data["gates"][1]["reward"].update(change)))


    def test_live_context_cannot_change_negotiated_protocol_version(self):
        from training import TrainingSession
        session = TrainingSession(self.manager, lambda *args: None, lambda *args: None)
        session.start(40, 153, self.values, {}, {})
        try:
            with self.assertRaisesRegex(ProtocolError, "version"):
                session.context(delivery_context(**{"training.version": 4}))
        finally:
            session.close()


    def test_project_order_fresh_canonical_stage_and_current_cursor_only(self):
        with self.assertRaisesRegex(ValueError, "previous canonical"):
            self.start(STAGE_TWO)
        first = self.start(STAGE_ONE)
        old = self.environments[-1]; old.files["personal"] = "no transfer"
        self.manager.checkpoint(self.identity, "reload")
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")
        self.assertTrue(old.closed)
        self.assertEqual(self.state()["project"], {"id":"delivery.nginx","next_stage":2})
        second = self.start(STAGE_TWO)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.environments[-1].files, {})
        self.assertEqual(self.environments[-1].preparation, "nginx-reload-v1")
        self.assertNotIn("education", self.state())

    def test_reset_and_checkpoint_bindings_do_not_bypass_required_reload(self):
        self.finish_one(); self.start(STAGE_TWO)
        old = self.environments[-1]; old.files["personal"] = "discard"
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            self.manager.checkpoint(self.identity, "restart")
        self.manager.checkpoint(self.identity, "reload")
        before = self.state()["attempt"]
        binding = {"attempt":before["id"],"generation":before["generation"],"content":before["course_digest"]}
        self.manager.reset(self.identity)
        self.assertTrue(old.closed)
        self.assertEqual(self.state()["project"]["next_stage"], 2)
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])
        self.assertEqual(self.environments[-1].files, {})
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.manager.checkpoint(self.identity, "reload", expected=binding)

    def test_wrong_preparation_is_facility_error_without_queue_penalty(self):
        self.manager.select_course(self.identity, STAGE_ONE)
        before = self.state()["assessment_queues"]
        self.wrong_preparation = True
        with self.assertRaisesRegex(RuntimeError, "preparation identity"):
            self.manager.prepare(self.identity)
        self.assertTrue(self.environments[-1].closed)
        self.assertEqual(self.state()["assessment_queues"], before)
        self.assertIsNone(self.state()["pending"])

    def test_reward_eligibility_always_uses_current_native_ownership(self):
        self.finish_one()
        for changes in ({"training.version":4},{"perk.training.enabled":0},{"perk.toughness":0},
                        {"perk.toughness":2},{"perk.toughness":4},{"perk.toughness.max":1}):
            self.manager.observe(self.identity, {}, delivery_context(**changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.manager.select_course(self.identity, STAGE_TWO)
        self.assertIsNone(self.state()["pending"])

    def test_lost_ownership_before_final_check_cannot_issue_perk(self):
        self.finish_one(); selected=self.start(STAGE_TWO)
        self.manager.checkpoint(self.identity,"reload"); self.answer_two()
        self.manager.observe(self.identity, {}, delivery_context(**{"perk.toughness":0}))
        self.assertEqual(self.manager.check(self.identity)["status"], "error")
        self.assertEqual(self.state()["attempt"]["id"], selected["id"])
        self.assertIsNone(self.state()["pending"])

    def test_one_current_pending_retries_then_duplicate_ack_never_restores_it(self):
        self.finish_one(); self.assertTrue(self.finish_two()["perk_rewarded"])
        pending=self.manager.pending_perks(self.identity,{})
        self.assertEqual(len(pending),1)
        self.assertEqual(self.manager.pending_perks(self.identity,{}),pending)
        for _ in range(2): self.manager.acknowledge_perk(self.identity,*pending[0],True)
        self.assertFalse(self.manager.pending_perks(self.identity,{}))
        self.manager.close(); self.manager=self.new_manager()
        self.manager.observe(self.identity,{},delivery_context()); self.manager.observe_perks(self.identity,{})
        self.assertFalse(self.manager.pending_perks(self.identity,{}))
        self.assertNotIn("perk_receipts",self.state())

    def test_lost_ack_native_claim_reopen_suppresses_current_pending(self):
        self.finish_one(); self.finish_two()
        pending=self.manager.pending_perks(self.identity,{})[0]
        self.manager.observe_perks(self.identity,{"12:2":pending[-1]})
        self.assertFalse(self.manager.pending_perks(self.identity,{}))
        with self.assertRaises(ProtocolError):
            self.manager.acknowledge_perk(self.identity,"55"*16,12,2,pending[-1],True)
        with self.assertRaises(ProtocolError):
            self.manager.acknowledge_perk(self.identity,self.identity,12,2,"77"*16,True)

    def test_failed_completion_write_leaves_no_pending_grant(self):
        self.finish_one(); selected=self.start(STAGE_TWO)
        self.manager.checkpoint(self.identity,"reload"); self.answer_two()
        with mock.patch.object(self.manager,"_save",side_effect=OSError("disk failure")):
            with self.assertRaises(OSError): self.manager.check(self.identity)
        self.assertEqual(self.state()["attempt"]["id"],selected["id"])
        self.assertFalse(self.manager.pending_perks(self.identity,{}))

    def test_new_runtime_discards_project_pending_and_can_earn_again(self):
        self.finish_one(); self.finish_two(); old=self.identity; other="22"*16
        values=delivery_context()
        for i in range(4): values[f"training.profile.{i}"]=int.from_bytes(bytes.fromhex(other)[i*4:i*4+4],"big",signed=True)
        self.manager.observe(other,{},values); self.manager.observe_perks(other,{})
        self.assertIsNone(self.manager.snapshot(other)["project"])
        self.assertFalse(self.manager.pending_perks(other,{}))
        with self.assertRaises(ProtocolError): self.manager.pending_perks(old,{})
        self.identity=other; self.finish_one(); self.assertTrue(self.finish_two()["perk_rewarded"])

    def test_native_perk_claim_never_creates_project_or_delivery(self):
        self.manager.observe_perks(self.identity,{"12:2":"66"*16})
        self.assertIsNone(self.state()["project"])
        self.assertFalse(self.manager.pending_perks(self.identity,{}))

    def test_old_snapshot_schema_rejected_and_character_receipt_collision_avoided(self):
        self.finish_one()
        self.manager.observe(self.identity,{"12:62000":{"receipt":"44"*16,"points":1}},self.values)
        self.start(STAGE_TWO); self.manager.checkpoint(self.identity,"reload"); self.answer_two()
        with mock.patch("secrets.token_hex",side_effect=["44"*16,"55"*16]) as generate:
            self.manager.check(self.identity)
        self.assertEqual(generate.call_count,2)
        self.assertEqual(self.state()["pending"]["receipt"],"55"*16)
        self.manager.close()
        self.manager.path.write_text(json.dumps({"version":4,"profiles":{self.identity:{"receipts":{}}}}))
        with self.assertRaisesRegex(ValueError,"old progression snapshots"): self.new_manager()


if __name__ == "__main__":
    unittest.main()
