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


    def test_educational_retry_cannot_silently_abandon_a_legacy_lab(self):
        self.complete_intro()
        attempt = self.manager.select(self.identity, 12, 61000)
        self.manager.prepare(self.identity)
        environment = self.environments[-1]
        with self.assertRaisesRegex(ValueError, "legacy"):
            self.manager.retry_course(self.identity)
        self.assertEqual(self.state()["attempt"]["id"], attempt["id"])
        self.assertFalse(environment.closed)


    def test_standalone_assessments_have_no_independent_achievement_prerequisites(self):
        self.assertTrue(all(item["eligible"] for item in self.manager.course_status(self.identity)))
        self.complete_intro()
        state = self.state()
        self.assertNotIn("education", state)
        self.assertNotIn("receipts", state)
        self.assertEqual(state["last_assessment"], "test.intro")
        self.assertFalse(next(item for item in self.manager.course_status(self.identity)
                              if item["id"] == "test.intro")["complete"])

    def test_draft_commitment_feedback_and_success_consumption_are_bounded(self):
        attempt = self.manager.select_course(self.identity, "test.intro")
        with self.assertRaisesRegex(ValueError, "withheld"):
            self.manager.course_feedback(self.identity)
        with self.assertRaisesRegex(ValueError, "Complete every"):
            self.manager.commit_answers(self.identity)
        self.assertIsNone(self.state()["attempt"]["conceptual"])
        self.answer_all()
        self.assertTrue(self.manager.commit_answers(self.identity)["passed"])
        self.manager.check(self.identity)
        pool = self.state()["assessment_queues"]["test.intro"]["pending"]
        self.assertNotIn(attempt["alternative"], pool)
        self.assertIsNone(self.state()["attempt"])

    def test_mixed_requires_current_practical_and_committed_conceptual_success(self):
        attempt = self.manager.select_course(self.identity, "test.mixed")
        self.manager.prepare(self.identity)
        env = self.environments[-1]
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        self.assertFalse(env.closed)
        self.answer_all()
        self.manager.commit_answers(self.identity)
        env.outcome = {"status": "incomplete", "feedback": "Protected data changed"}
        self.assertEqual(self.manager.check(self.identity)["status"], "incomplete")
        self.assertEqual(env.checks, 2)
        self.assertFalse(self.state()["pending"])
        self.assertIn(attempt["alternative"], self.state()["assessment_queues"]["test.mixed"]["pending"])

    def test_ordered_checkpoint_and_reset_bind_to_current_attempt_generation(self):
        attempt = self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        self.assertEqual(self.manager.check(self.identity)["status"], "pending")
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            self.manager.checkpoint(self.identity, "reapply")
        binding = {"attempt": attempt["id"], "generation": attempt["generation"], "content": attempt["course_digest"]}
        self.manager.checkpoint(self.identity, "rollback", expected=binding)
        self.manager.reset(self.identity)
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.manager.checkpoint(self.identity, "rollback", expected=binding)
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])
        self.manager.checkpoint(self.identity, "rollback")
        self.manager.checkpoint(self.identity, "reapply")
        self.assertEqual(self.manager.check(self.identity)["status"], "passed")

    def test_checkpoint_facility_error_preserves_queue_without_reward_or_failure(self):
        self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        before = self.state()["assessment_queues"]
        env = self.environments[-1]
        env.checkpoint_outcome = {"status": "error", "facility_failure": True}
        self.assertEqual(self.manager.checkpoint(self.identity, "rollback")["status"], "error")
        self.assertEqual(self.state()["assessment_queues"], before)
        self.assertIsNone(self.state()["pending"])
        self.assertEqual(self.state()["attempt"]["status"], "provider_lost")
        self.manager.reset(self.identity)
        self.assertEqual(self.state()["attempt"]["status"], "active")

    def test_checkpoint_rejects_forgery_oversize_and_late_cancel(self):
        self.manager.select_course(self.identity, "test.ordered")
        self.manager.prepare(self.identity)
        env = self.environments[-1]
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.manager.checkpoint(self.identity, "rollback", expected={"evidence": "forged"})
        env.checkpoint_outcome = {"status": "passed", "evidence": {"blob": "x"*5000}}
        self.assertEqual(self.manager.checkpoint(self.identity, "rollback")["status"], "error")
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])
        def interrupted(name):
            self.manager.cancel(self.identity)
            return {"status": "passed"}
        env.checkpoint = interrupted
        self.assertEqual(self.manager.checkpoint(self.identity, "rollback")["status"], "error")
        self.assertEqual(self.state()["attempt"]["checkpoints"], [])

    def test_new_runtime_discards_drafts_attempt_queue_and_old_callbacks(self):
        self.manager.select_course(self.identity, "test.intro")
        self.manager.draft_answer(self.identity, "q1", "1")
        other = "22"*16
        values = dict(self.values)
        for i in range(4):
            values[f"training.profile.{i}"] = int.from_bytes(bytes.fromhex(other)[i*4:i*4+4], "big", signed=True)
        self.manager.observe(other, {}, values)
        state = self.manager.snapshot(other)
        self.assertIsNone(state["attempt"])
        self.assertEqual(state["assessment_queues"], {})
        with self.assertRaises(wire.ProtocolError):
            self.manager.questions(self.identity)


if __name__ == "__main__":
    unittest.main()
