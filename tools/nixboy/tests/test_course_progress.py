"""Focused manager contracts; real environment acceptance lives in the smoke tool."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from challenge_content import Catalog
import protocol as wire
from training import AttemptManager, TrainingSession
from test_training import FakeEnvironment, context
from test_bridge import FakeClient


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "content-assessment-test"


class CourseEnvironment(FakeEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.checks = 0
        self.observed = []
        self.facility = {"status": "ready"}
        self.checkpoint_outcome = None

    def check(self):
        self.checks += 1
        return self.outcome

    def health(self):
        return self.facility

    def checkpoint(self, name):
        self.observed.append(name)
        return self.checkpoint_outcome or {
            "status": "passed", "feedback": "Observed fixture state", "evidence": {"observed": name}}


class CourseProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.catalog = Catalog(FIXTURE_ROOT)
        self.environments = []
        self.clients = mock.Mock(side_effect=FakeClient)
        self.manager = self.new_manager()
        self.addCleanup(lambda: self.manager.close())
        self.identity, _ = wire.training_context(context())
        self.values = context(science=10)
        self.manager.observe(self.identity, {}, self.values)

    def create_environment(self, *args, **kwargs):
        environment = CourseEnvironment(*args, **kwargs)
        self.environments.append(environment)
        return environment

    def new_manager(self):
        return AttemptManager(self.directory.name, self.clients, self.catalog, self.create_environment)

    def state(self):
        return self.manager.snapshot(self.identity)

    def answer_all(self, correct=True):
        attempt = self.state()["attempt"]
        alternative = self.catalog.course.alternative(attempt["course_gate"], attempt["alternative"])
        for question in alternative.questions:
            answer = question.answers[0] if correct else (
                "1" if question.type == "choice" and question.answers[0] != "1" else
                "2" if question.type == "choice" else "999" if question.type == "number" else "wrong")
            self.manager.draft_answer(self.identity, question.id, answer)

    def complete_intro(self):
        self.manager.select_course(self.identity, "test.intro")
        self.answer_all()
        self.assertTrue(self.manager.commit_answers(self.identity)["passed"])
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")

    def test_explicit_prerequisites_ignore_game_percentages_and_all_parent_needs_every_component(self):
        high = context(science=300)
        self.manager.observe(self.identity, {}, high)
        with self.assertRaisesRegex(ValueError, "prerequisites"):
            self.manager.select_course(self.identity, "test.mixed")
        with self.assertRaisesRegex(ValueError, "ALL"):
            self.manager.select_course(self.identity, "test.foundation")
        self.complete_intro()
        statuses = {item["id"]: item for item in self.manager.course_status(self.identity)}
        self.assertTrue(statuses["test.mixed"]["eligible"])
        self.assertFalse(statuses["test.foundation"]["complete"])
        # A gate completion, rather than a percentage change, unlocked the mixed fixture.
        self.assertEqual(self.manager.contexts[self.identity]["skill.science"], 300)
        self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        self.answer_all()
        self.manager.commit_answers(self.identity)
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")
        self.assertTrue(next(item for item in self.manager.course_status(self.identity)
                             if item["id"] == "test.foundation")["complete"])
        self.assertFalse(self.state()["receipts"])

    def test_theory_only_never_opens_client_or_environment_even_at_prepare_and_check(self):
        self.manager.select_course(self.identity, "test.intro")
        self.assertIsNone(self.manager.prepare(self.identity))
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        self.answer_all()
        self.manager.commit_answers(self.identity)
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")
        self.clients.assert_not_called()
        self.assertFalse(self.environments)
        self.assertEqual(self.manager.pending_rewards(self.identity, {}), [])

    def test_drafts_incomplete_sets_and_premature_feedback_are_nonfinal(self):
        original = self.manager.select_course(self.identity, "test.intro")
        before = self.state()["education"]["queues"]
        public = self.manager.questions(self.identity)
        self.assertIsNone(public["feedback"])
        self.assertNotIn("answers", public["questions"][0])
        with self.assertRaisesRegex(ValueError, "withheld"):
            self.manager.course_feedback(self.identity)
        with self.assertRaisesRegex(ValueError, "Complete every"):
            self.manager.commit_answers(self.identity)
        question = self.catalog.course.alternative("test.intro", original["alternative"]).questions[0]
        self.manager.draft_answer(self.identity, question.id, question.answers[0])
        with self.assertRaisesRegex(ValueError, "Complete every"):
            self.manager.commit_answers(self.identity)
        with self.assertRaises(ValueError):
            self.manager.draft_answer(self.identity, question.id, "999")
        self.assertEqual(self.state()["attempt"]["id"], original["id"])
        self.assertIsNone(self.state()["attempt"]["conceptual"])
        self.assertEqual(self.state()["education"]["queues"], before)
        self.assertFalse(self.state()["education"]["completions"])

    def test_commitment_is_durable_and_reconnect_cannot_edit_committed_answers(self):
        self.manager.select_course(self.identity, "test.intro")
        self.answer_all()
        result = self.manager.commit_answers(self.identity)
        committed = self.state()["attempt"]
        self.manager.detach_frontend(self.identity)
        self.manager.attach_frontend(self.identity)
        self.assertEqual(self.manager.course_feedback(self.identity), result)
        with self.assertRaisesRegex(ValueError, "committed"):
            self.manager.draft_answer(self.identity, "q1", "1")
        self.assertEqual(self.manager.commit_answers(self.identity), result)
        self.manager.close()
        self.manager = self.new_manager()
        self.manager.observe(self.identity, {}, self.values)
        self.assertEqual(self.manager.questions(self.identity)["answers"], committed["answers"])
        self.assertTrue(self.manager.questions(self.identity)["committed"])
        self.assertEqual(self.manager.course_feedback(self.identity), result)

    def test_retry_prefers_unexposed_alternative_then_honestly_marks_exposed_fallback(self):
        first = self.manager.select_course(self.identity, "test.intro")
        self.answer_all(correct=False)
        self.assertFalse(self.manager.commit_answers(self.identity)["passed"])
        second = self.manager.retry_course(self.identity)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["alternative"], second["alternative"])
        self.assertFalse(second["exposed_retry"])
        self.answer_all(correct=False)
        self.manager.commit_answers(self.identity)
        fallback = self.manager.retry_course(self.identity)
        self.assertNotIn(fallback["id"], (first["id"], second["id"]))
        self.assertTrue(fallback["exposed_retry"])
        self.assertIsNone(fallback["conceptual"])
        self.assertEqual(fallback["answers"], {})

    def test_interruption_between_commit_and_grading_cannot_reopen_answers(self):
        self.manager.select_course(self.identity, "test.intro")
        self.answer_all()
        with mock.patch.object(self.catalog.course, "grade", side_effect=RuntimeError("grading interrupted")):
            with self.assertRaisesRegex(RuntimeError, "grading interrupted"):
                self.manager.commit_answers(self.identity)
        saved = json.loads(self.manager.path.read_text(encoding="utf-8"))
        commitment = saved["profiles"][self.identity]["attempt"]["conceptual"]
        self.assertTrue(commitment["committed"])
        self.assertFalse(commitment["graded"])
        self.manager.close()
        self.manager = self.new_manager()
        with self.assertRaisesRegex(ValueError, "committed"):
            self.manager.draft_answer(self.identity, "q1", "1")
        self.assertTrue(self.manager.course_feedback(self.identity)["passed"])
        self.assertTrue(self.state()["attempt"]["conceptual"]["graded"])
        self.clients.assert_not_called()

    def test_practical_only_submission_preserves_mixed_attempt_and_environment(self):
        self.complete_intro()
        attempt = self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        environment = self.environments[-1]
        before = self.state()["education"]["queues"]
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        self.assertFalse(environment.closed)
        self.assertEqual(self.state()["attempt"]["id"], attempt["id"])
        self.assertEqual(self.state()["education"]["queues"], before)
        self.assertNotIn("test.mixed", self.state()["education"]["completions"])
        self.assertFalse(self.state()["receipts"])

    def test_conceptual_only_success_cannot_complete_and_finalization_rechecks_mutable_practical_state(self):
        self.complete_intro()
        self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        environment = self.environments[-1]
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        self.answer_all()
        self.assertTrue(self.manager.commit_answers(self.identity)["passed"])
        environment.outcome = {"status": "incomplete", "feedback": "Required data removed after P submission"}
        result = self.manager.check(self.identity)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(environment.checks, 2)
        self.assertNotIn("test.mixed", self.state()["education"]["completions"])
        self.assertFalse(self.state()["receipts"])

    def test_ordered_checkpoints_reject_wrong_order_stale_binding_and_final_state_only(self):
        self.complete_intro()
        attempt = self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        before = self.state()["education"]["queues"]
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        self.assertEqual(self.environments[-1].observed, [])
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            self.manager.checkpoint(self.identity, "reapply")
        binding = {"attempt": attempt["id"], "generation": attempt["generation"], "content": attempt["course_digest"]}
        self.manager.checkpoint(self.identity, "rollback", expected=binding)
        first = self.state()["attempt"]["checkpoints"][0]
        self.assertEqual({key: first[key] for key in binding}, binding)
        self.manager.reset(self.identity)
        reset = self.state()["attempt"]
        self.assertEqual(reset["generation"], attempt["generation"] + 1)
        self.assertEqual(reset["checkpoints"], [])
        with self.assertRaisesRegex(ValueError, "Stale or cross-attempt"):
            self.manager.checkpoint(self.identity, "rollback", expected=binding)
        self.assertEqual(self.environments[-1].observed, [])
        self.assertEqual(self.state()["education"]["queues"], before)
        self.manager.checkpoint(self.identity, "rollback")
        self.manager.checkpoint(self.identity, "reapply")
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")

    def test_incomplete_or_oversized_checkpoint_is_nonfinal_and_untrusted_claim_is_rejected(self):
        self.complete_intro()
        attempt = self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        environment = self.environments[-1]
        before = self.state()["education"]["queues"]
        with self.assertRaisesRegex(ValueError, "Stale or cross-attempt"):
            self.manager.checkpoint(self.identity, "rollback", expected={"status": "passed", "evidence": "forged"})
        environment.checkpoint_outcome = {"status": "incomplete", "feedback": "Actual policy is still new"}
        self.assertEqual(self.manager.checkpoint(self.identity, "rollback")["status"], "incomplete")
        environment.checkpoint_outcome = {"status": "passed", "evidence": {"blob": "x" * 5000}}
        self.assertEqual(self.manager.checkpoint(self.identity, "rollback")["status"], "error")
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])
        self.assertEqual(self.state()["attempt"]["id"], attempt["id"])
        self.assertEqual(self.state()["education"]["queues"], before)
        self.assertFalse(environment.closed)

    def test_facility_interruption_keeps_queue_and_completion_and_requires_reissue(self):
        self.complete_intro()
        self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        before = self.state()["education"]["queues"]
        self.environments[-1].checkpoint_outcome = {
            "status": "error", "facility_failure": True, "feedback": "Prepared worker lost"}
        self.assertEqual(self.manager.checkpoint(self.identity, "rollback")["status"], "error")
        self.assertEqual(self.state()["attempt"]["status"], "provider_lost")
        self.assertEqual(self.state()["education"]["queues"], before)
        self.assertIn("test.intro", self.state()["education"]["completions"])
        self.assertFalse(self.state()["receipts"])
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.manager.prepare(self.identity)
        self.manager.reset(self.identity)
        self.assertEqual(self.state()["attempt"]["status"], "active")

    def test_completion_survives_older_game_context_reset_restart_and_compatible_catalog_reload(self):
        self.complete_intro()
        completion = self.state()["education"]["completions"]["test.intro"]
        self.manager.observe(self.identity, {}, context(science=0))
        self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        self.manager.reset(self.identity)
        self.manager.abandon(self.identity)
        self.manager.close()
        self.catalog = Catalog(FIXTURE_ROOT)
        self.manager = self.new_manager()
        self.manager.observe(self.identity, {}, context(science=0))
        self.assertEqual(self.state()["education"]["completions"]["test.intro"], completion)
        self.assertTrue(next(item for item in self.manager.course_status(self.identity)
                             if item["id"] == "test.mixed")["eligible"])

    def test_provider_restart_cannot_silently_recreate_an_active_practical_attempt(self):
        self.complete_intro()
        attempt = self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        self.manager.checkpoint(self.identity, "rollback")
        queue = self.state()["education"]["queues"]
        self.manager.close()
        self.manager = self.new_manager()
        self.manager.observe(self.identity, {}, self.values)
        self.assertEqual(self.state()["attempt"]["status"], "provider_lost")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.manager.prepare(self.identity)
        self.assertEqual(self.manager.check(self.identity)["status"], "error")
        self.assertEqual(self.state()["education"]["queues"], queue)
        self.assertIn("test.intro", self.state()["education"]["completions"])
        self.manager.reset(self.identity)
        self.assertEqual(self.state()["attempt"]["generation"], attempt["generation"] + 1)
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])

    def test_theory_reset_advances_generation_and_clears_only_uncommitted_drafts(self):
        attempt = self.manager.select_course(self.identity, "test.intro")
        question = self.catalog.course.alternative("test.intro", attempt["alternative"]).questions[0]
        self.manager.draft_answer(self.identity, question.id, question.answers[0])
        self.assertIsNone(self.manager.reset(self.identity))
        reset = self.state()["attempt"]
        self.assertEqual(reset["id"], attempt["id"])
        self.assertEqual(reset["generation"], attempt["generation"] + 1)
        self.assertEqual(reset["answers"], {})
        self.assertIsNone(reset["conceptual"])
        self.clients.assert_not_called()

    def interrupt_checkpoint(self, close_manager):
        self.complete_intro()
        self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        entered, release, provider_marked = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        def observe(name):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test observer was not released")
            return {"status": "passed", "evidence": {"observed": name}}
        def checkpoint():
            try:
                results.append(self.manager.checkpoint(self.identity, "rollback"))
            except Exception as exc:
                errors.append(exc)
        original = self.manager._provider_stopped
        def mark(attempt):
            original(attempt)
            provider_marked.set()
        worker = threading.Thread(target=checkpoint)
        closing = None
        with mock.patch.object(self.environments[-1], "checkpoint", side_effect=observe), \
                mock.patch.object(self.manager, "_provider_stopped", side_effect=mark):
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                if close_manager:
                    closing = threading.Thread(target=self.manager.close)
                    closing.start()
                    self.assertTrue(provider_marked.wait(2))
                else:
                    with self.manager.lock:
                        self.manager._provider_stopped(self.manager._profile(self.identity)["attempt"])
                        self.manager._save()
            finally:
                release.set()
                worker.join(4)
                if closing:
                    closing.join(4)
        self.assertFalse(worker.is_alive())
        if closing:
            self.assertFalse(closing.is_alive())
        self.assertFalse(errors)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("interrupted", results[0]["feedback"])
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])
        self.assertEqual(self.state()["attempt"]["status"], "provider_lost")
        self.assertIn("test.intro", self.state()["education"]["completions"])

    def test_checkpoint_observation_cannot_commit_after_concurrent_manager_close(self):
        self.interrupt_checkpoint(close_manager=True)

    def test_checkpoint_observation_cannot_commit_after_concurrent_provider_loss(self):
        self.interrupt_checkpoint(close_manager=False)

    def test_committed_mixed_facility_loss_directs_explicit_retry_instead_of_forbidden_reset(self):
        self.complete_intro()
        self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        self.answer_all()
        self.manager.commit_answers(self.identity)
        self.environments[-1].outcome = {
            "status": "error", "facility_failure": True, "feedback": "Prepared environment stopped"}
        self.assertEqual(self.manager.check(self.identity)["status"], "error")
        interrupted = self.state()["attempt"]
        self.assertEqual(interrupted["status"], "provider_lost")
        self.assertIn("use T", interrupted["facility_error"])
        self.assertNotIn("F7", interrupted["facility_error"])
        with self.assertRaisesRegex(ValueError, "use T"):
            self.manager.reset(self.identity)

    def test_question_input_preserves_exact_answer_spaces_and_case_but_trims_commands(self):
        session = TrainingSession(self.manager, mock.Mock(), mock.Mock())
        session.identity, session.mode = self.identity, "questions"
        with mock.patch.object(self.manager, "draft_answer") as draft, \
                mock.patch.object(session, "question_menu"):
            session.input(b"q2  ExAcT  \r")
            draft.assert_called_once_with(self.identity, "q2", " ExAcT  ")
        with mock.patch.object(session, "hub") as hub:
            session.input(b"  H  \r")
            hub.assert_called_once_with()

    def test_content_change_rejects_unfinished_answers_and_checkpoints_without_erasing_completion(self):
        self.complete_intro()
        attempt = self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        self.manager.checkpoint(self.identity, "rollback")
        completion = self.state()["education"]["completions"]["test.intro"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "changed"
            shutil.copytree(FIXTURE_ROOT, root)
            path = root / "course.yaml"
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            next(gate for gate in raw["gates"] if gate["id"] == "test.ordered")["version"] += 1
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            self.manager.catalog = Catalog(root)
            with self.assertRaisesRegex(ValueError, "content changed"):
                self.manager.checkpoint(self.identity, "reapply")
            self.assertEqual(self.manager.check(self.identity)["status"], "error")
        self.assertEqual(self.state()["attempt"]["id"], attempt["id"])
        self.assertEqual(self.state()["education"]["completions"]["test.intro"], completion)
        self.assertNotIn("test.ordered", self.state()["education"]["completions"])

    def test_legacy_selection_cannot_bypass_course_requirements_but_legacy_receipts_still_work(self):
        pool = self.catalog.pool(12, 61000)
        self.assertFalse(set(pool) & self.catalog.course.challenge_ids)
        self.assertTrue(pool)
        attempt = self.manager.select(self.identity, 12, 61000)
        self.assertNotIn(attempt["challenge"], self.catalog.course.challenge_ids)
        self.manager.prepare(self.identity)
        result = self.manager.check(self.identity)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["rewarded"])
        receipt = self.state()["receipts"]["12:61000"]
        self.assertEqual(receipt["points"], 1)
        self.manager.observe(self.identity, {}, context(science=10))
        self.assertEqual(self.manager.pending_rewards(self.identity, {})[0][3], receipt["receipt"])
        self.manager.observe(self.identity, {"12:61000": receipt}, context(science=11))
        self.assertEqual(self.manager.pending_rewards(self.identity, {"12:61000": receipt}), [])


    def test_educational_retry_cannot_silently_abandon_a_legacy_lab(self):
        self.complete_intro()
        attempt = self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        environment = self.environments[-1]
        with self.assertRaisesRegex(ValueError, "legacy"):
            self.manager.retry_course(self.identity)
        self.assertEqual(self.state()["attempt"]["id"], attempt["id"])
        self.assertFalse(environment.closed)

    def test_late_practical_result_cannot_complete_after_provider_loss(self):
        self.complete_intro()
        self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        self.answer_all()
        self.manager.commit_answers(self.identity)
        def interrupted():
            self.manager._provider_stopped(self.manager.data["profiles"][self.identity]["attempt"])
            return {"status": "passed"}
        self.environments[-1].check = interrupted
        self.assertEqual(self.manager.check(self.identity)["status"], "error")
        self.assertNotIn("test.mixed", self.state()["education"]["completions"])


if __name__ == "__main__":
    unittest.main()
