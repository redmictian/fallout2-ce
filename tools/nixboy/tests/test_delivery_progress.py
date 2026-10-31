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

    def test_project_schema_rejects_unknown_dependencies_duplicate_and_skipped_stages(self):
        changes = [
            lambda data: data["gates"][1].update(prerequisites=["delivery.unknown"]),
            lambda data: data["gates"][1]["project"].update(stage=1),
            lambda data: data["gates"][1]["project"].update(stage=3),
            lambda data: data["gates"][1].update(prerequisites=[]),
            lambda data: data["gates"][1]["project"].update(preparation="unbound-preparation"),
        ]
        for mutate in changes:
            with self.subTest(change=mutate), self.assertRaises(ValueError):
                Catalog(self.mutate_catalog(mutate))

    def test_test_only_perk_schema_rejects_other_types_ranks_and_unscoped_mapping(self):
        for change in ({"type": "skill"}, {"perk": 13}, {"target_rank": 3}, {"test_only": False}, {"perk": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Catalog(self.mutate_catalog(lambda data: data["gates"][1]["reward"].update(change)))

    def test_later_stage_cannot_be_selected_through_skill_percentage_or_skipped_completion(self):
        self.manager.observe(self.identity, {}, delivery_context(**{"skill.science": 300}))
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            self.manager.select_course(self.identity, STAGE_TWO)
        self.clients.assert_not_called()
        self.assertIsNone(self.state()["attempt"])
        self.assertFalse(self.state()["perk_receipts"])

    def test_stage_one_is_removed_and_stage_two_is_a_distinct_canonical_environment(self):
        first = self.start(STAGE_ONE)
        environment_one = self.environments[-1]
        environment_one.files["learner-witness"] = "must not carry"
        self.manager.checkpoint(self.identity, "reload")
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")
        self.assertTrue(environment_one.closed)
        self.assertNotIn(self.identity, self.manager.environments)
        second = self.start(STAGE_TWO)
        environment_two = self.environments[-1]
        self.assertIsNot(environment_two, environment_one)
        self.assertNotIn("learner-witness", environment_two.files)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["preparation_identity"], second["preparation_identity"])
        self.assertEqual(environment_two.preparation, "nginx-reload-v1")
        record = self.state()["education"]["completions"][STAGE_ONE]
        self.assertEqual(record["project"], {"id": "delivery.nginx", "stage": 1, "preparation": "nginx-policy-v1"})
        self.assertEqual(record["preparation_identity"], first["preparation_identity"])
        self.assertFalse(self.manager.pending_perks(self.identity, {}))

    def test_reset_stage_two_preserves_prior_completion_and_invalidates_checkpoint_binding(self):
        self.finish_one()
        selected = self.start(STAGE_TWO)
        original_completion = self.state()["education"]["completions"][STAGE_ONE]
        environment = self.environments[-1]
        environment.files["work"] = "discard on reset"
        self.manager.checkpoint(self.identity, "reload")
        before = self.state()["attempt"]
        binding = {"attempt": before["id"], "generation": before["generation"], "content": before["course_digest"]}
        self.manager.reset(self.identity)
        after = self.state()["attempt"]
        self.assertTrue(environment.closed)
        self.assertEqual(after["id"], selected["id"])
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertEqual(after["checkpoints"], [])
        self.assertEqual(self.environments[-1].files, {})
        self.assertEqual(self.state()["education"]["completions"][STAGE_ONE], original_completion)
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.manager.checkpoint(self.identity, "reload", expected=binding)
        self.assertFalse(self.state()["perk_receipts"])

    def test_final_state_and_wrong_checkpoint_order_do_not_bypass_reload_observation(self):
        self.start(STAGE_ONE)
        before = self.state()
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            self.manager.checkpoint(self.identity, "restart")
        self.assertEqual(self.state()["education"]["queues"], before["education"]["queues"])
        self.assertFalse(self.environments[-1].closed)
        self.assertNotIn(STAGE_ONE, self.state()["education"]["completions"])

    def test_wrong_canonical_start_is_facility_failure_without_rotation_or_reward(self):
        self.manager.select_course(self.identity, STAGE_ONE)
        before = self.state()
        self.wrong_preparation = True
        with self.assertRaisesRegex(RuntimeError, "preparation identity"):
            self.manager.prepare(self.identity)
        after = self.state()
        self.assertEqual(after["attempt"]["status"], "provider_lost")
        self.assertEqual(after["education"]["queues"], before["education"]["queues"])
        self.assertNotIn(STAGE_ONE, after["education"]["completions"])
        self.assertFalse(after["perk_receipts"])
        self.assertTrue(self.environments[-1].closed)

    def test_preparation_identity_cannot_change_between_start_and_final_submission(self):
        self.start(STAGE_ONE)
        self.manager.checkpoint(self.identity, "reload")
        before = self.state()
        self.environments[-1].preparation_identity = "a-different-stage"
        result = self.manager.check(self.identity)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["facility_failure"])
        after = self.state()
        self.assertEqual(after["education"]["queues"], before["education"]["queues"])
        self.assertNotIn(STAGE_ONE, after["education"]["completions"])
        self.assertFalse(after["perk_receipts"])

    def test_reward_eligibility_requires_v5_module_ownership_and_available_rank(self):
        self.finish_one()
        for changes in ({"training.version": 4}, {"perk.training.enabled": 0},
                        {"perk.toughness": 0}, {"perk.toughness": 2}, {"perk.toughness": 4},
                        {"perk.toughness.max": 1}):
            self.manager.observe(self.identity, {}, delivery_context(**changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.manager.select_course(self.identity, STAGE_TWO)
            self.assertIsNone(self.state()["attempt"])
        self.assertFalse(self.state()["perk_receipts"])

    def test_lost_ownership_during_attempt_preserves_attempt_without_completion_or_receipt(self):
        self.finish_one()
        selected = self.start(STAGE_TWO)
        self.manager.checkpoint(self.identity, "reload")
        self.answer_two()
        queues = self.state()["education"]["queues"]
        self.manager.observe(self.identity, {}, delivery_context(**{"perk.toughness": 0}))
        self.assertEqual(self.manager.check(self.identity)["status"], "error")
        after = self.state()
        self.assertEqual(after["attempt"]["id"], selected["id"])
        self.assertEqual(after["education"]["queues"], queues)
        self.assertNotIn(STAGE_TWO, after["education"]["completions"])
        self.assertFalse(after["perk_receipts"])

    def test_valid_completion_persists_education_and_exactly_one_perk_receipt_before_delivery(self):
        self.finish_one()
        self.assertTrue(self.finish_two()["perk_rewarded"])
        persisted = json.loads(self.manager.path.read_text())
        profile = persisted["profiles"][self.identity]
        self.assertIn(STAGE_TWO, profile["education"]["completions"])
        self.assertEqual(set(profile["perk_receipts"]), {"12:2"})
        entry = profile["perk_receipts"]["12:2"]
        completion = profile["education"]["completions"][STAGE_TWO]
        self.assertEqual(entry["profile"], self.identity)
        self.assertEqual(entry["gate"], STAGE_TWO)
        self.assertEqual((entry["attempt"], entry["generation"], entry["content"]),
                         (completion["attempt"], completion["generation"], completion["content"]))
        self.assertTrue(entry["test_only"])
        self.assertFalse(profile["receipts"])
        expected = [(self.identity, 12, 2, entry["receipt"])]
        self.assertEqual(self.manager.pending_perks(self.identity, {}), expected)
        self.assertEqual(self.manager.pending_perks(self.identity, {}), expected)  # Lost ACK.
        self.assertFalse(self.finish_two()["perk_rewarded"])
        self.assertEqual(self.manager.pending_perks(self.identity, {}), expected)

    def test_failed_completion_write_cannot_expose_an_unpersisted_perk_reward(self):
        self.finish_one()
        selected = self.start(STAGE_TWO)
        self.manager.checkpoint(self.identity, "reload")
        self.answer_two()
        with mock.patch.object(self.manager, "_save", side_effect=OSError("fixture disk write failure")):
            with self.assertRaises(OSError):
                self.manager.check(self.identity)
        self.assertEqual(self.state()["attempt"]["id"], selected["id"])
        self.assertNotIn(STAGE_TWO, self.state()["education"]["completions"])
        self.assertEqual(self.manager.pending_perks(self.identity, {}), [])

    def test_duplicate_ack_reopen_and_older_same_profile_context_reconcile_original_receipt(self):
        self.finish_one()
        self.finish_two()
        earned = self.manager.pending_perks(self.identity, {})[0]
        for _ in range(2):
            self.manager.acknowledge_perk(self.identity, *earned, True)
        receipt = earned[-1]
        self.assertFalse(self.manager.pending_perks(self.identity, {"12:2": receipt}))
        self.manager.close()
        self.manager = self.new_manager()
        self.manager.observe(self.identity, {}, delivery_context())
        self.manager.observe_perks(self.identity, {})
        self.assertIn(STAGE_TWO, self.state()["education"]["completions"])
        self.assertEqual(self.manager.pending_perks(self.identity, {}), [earned])
        self.manager.observe_perks(self.identity, {"12:2": receipt})
        self.assertFalse(self.manager.pending_perks(self.identity, {"12:2": receipt}))

    def test_conflicting_wrong_profile_and_cross_kind_perk_receipts_are_rejected(self):
        self.finish_one()
        self.finish_two()
        earned = self.manager.pending_perks(self.identity, {})[0]
        receipt = earned[-1]
        with self.assertRaises(ProtocolError):
            self.manager.observe_perks(self.identity, {"12:2": "77" * 16})
        with self.assertRaises(ProtocolError):
            self.manager.pending_perks(self.identity, {"12:2": "77" * 16})
        with self.assertRaises(ProtocolError):
            self.manager.acknowledge_perk(self.identity, "55" * 16, 12, 2, receipt, True)
        with self.assertRaises(ProtocolError):
            self.manager.acknowledge_perk("55" * 16, self.identity, 12, 2, receipt, True)
        with self.assertRaises(ProtocolError):
            self.manager.observe(self.identity, {"12:62000": {"receipt": receipt, "points": 1}}, self.values)
        with self.assertRaises(ProtocolError):
            self.manager.observe_perks(self.identity, {"13:2": receipt})
        with self.assertRaises(ProtocolError):
            self.manager.observe_perks(self.identity, {"12:3": receipt})
        self.assertEqual(self.manager.pending_perks(self.identity, {}), [earned])

    def test_game_perk_import_does_not_invent_project_completion_or_accept_skill_receipt_reuse(self):
        self.manager.observe_perks(self.identity, {"12:2": "66" * 16})
        self.assertFalse(self.state()["education"]["completions"] if "education" in self.state() else {})
        self.assertEqual(self.manager.pending_perks(self.identity, {}), [(self.identity, 12, 2, "66" * 16)])
        other = "22" * 16
        values = delivery_context()
        for index in range(4):
            values[f"training.profile.{index}"] = int.from_bytes(bytes.fromhex(other)[index*4:index*4+4], "big", signed=True)
        self.manager.observe(other, {"12:62000": {"receipt": "44" * 16, "points": 1}}, values)
        with self.assertRaises(ProtocolError):
            self.manager.observe_perks(other, {"12:2": "44" * 16})

    def test_old_bridge_v4_profile_without_perk_fields_preserves_skills_and_education(self):
        self.finish_one()
        self.manager.observe(self.identity, {"12:62000": {"receipt": "33" * 16, "points": 1}}, self.values)
        expected = self.state()
        self.manager.close()
        data = json.loads(self.manager.path.read_text())
        self.assertEqual(data["version"], 4)
        data["profiles"][self.identity].pop("perk_receipts", None)
        self.manager.path.write_text(json.dumps(data))
        self.manager = self.new_manager()
        self.manager.observe(self.identity, {}, context(science=10))
        actual = self.state()
        self.assertEqual(actual["education"]["completions"], expected["education"]["completions"])
        self.assertEqual(actual["receipts"], expected["receipts"])
        self.assertEqual(self.manager.pending_perks(self.identity, {}), [])
        self.assertEqual(self.manager.pending_rewards(self.identity, {})[0][3], "33" * 16)

    def test_perk_receipt_generation_avoids_existing_skill_receipt_identity(self):
        self.finish_one()
        self.manager.observe(self.identity, {"12:62000": {"receipt": "44" * 16, "points": 1}}, self.values)
        self.start(STAGE_TWO)
        self.manager.checkpoint(self.identity, "reload")
        self.answer_two()
        with mock.patch("perk_rewards.secrets.token_hex", side_effect=["44" * 16, "88" * 16]) as generate:
            self.assertTrue(self.manager.check(self.identity)["perk_rewarded"])
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(self.manager.pending_perks(self.identity, {})[0][-1], "88" * 16)
        self.assertEqual(self.state()["receipts"]["12:62000"]["receipt"], "44" * 16)

    def test_recovery_rejects_persisted_wrong_profile_and_cross_kind_records(self):
        self.finish_one()
        self.finish_two()
        self.manager.close()
        path = self.manager.path
        original = json.loads(path.read_text())
        wrong_profile = json.loads(json.dumps(original))
        wrong_profile["profiles"][self.identity]["perk_receipts"]["12:2"]["profile"] = "55" * 16
        reused = json.loads(json.dumps(original))
        receipt = reused["profiles"][self.identity]["perk_receipts"]["12:2"]["receipt"]
        reused["profiles"][self.identity]["receipts"]["12:62000"] = {"receipt": receipt, "points": 1}
        for broken in (wrong_profile, reused):
            path.write_text(json.dumps(broken))
            with self.assertRaises(ValueError):
                self.new_manager()
        path.write_text(json.dumps(original))


if __name__ == "__main__":
    unittest.main()
