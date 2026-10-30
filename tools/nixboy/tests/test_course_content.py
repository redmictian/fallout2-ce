import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge_content import Catalog, CONTENT_ROOT
from course_content import CourseCatalog, Question, normalize_answer


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "content-assessment-test"


class CourseCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog(FIXTURE_ROOT)
        self.course = self.catalog.course

    def mutate(self, edit):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "catalog"
        shutil.copytree(FIXTURE_ROOT, root)
        path = root / "course.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        edit(raw)
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return root

    def test_legacy_catalog_has_no_course_and_still_loads_twenty_tasks(self):
        legacy = Catalog(CONTENT_ROOT)
        self.assertIsNone(legacy.course)
        self.assertEqual(len(legacy.challenges), 20)

    def test_fixture_formats_and_explicit_prerequisite_are_independent_of_skills(self):
        self.assertEqual(set(self.course.gates),
                         {"test.intro", "test.mixed", "test.ordered", "test.foundation"})
        self.assertEqual(self.course.get("test.intro").format, "theory")
        self.assertIsNone(self.course.alternative("test.intro", "a").challenge)
        self.assertFalse(self.course.eligible("test.mixed", {}))
        self.assertTrue(self.course.eligible("test.mixed", {"test.intro": {"attempt": "earned"}}))
        self.assertEqual(self.course.missing_prerequisites("test.mixed", set()), ("test.intro",))

    def test_all_parent_needs_every_component_and_cannot_be_forged_by_parent_presence(self):
        self.assertFalse(self.course.is_complete("test.foundation", {"test.foundation"}))
        self.assertFalse(self.course.is_complete("test.foundation", {"test.intro"}))
        self.assertTrue(self.course.is_complete("test.foundation", {"test.intro", "test.mixed"}))

    def test_public_question_projection_contains_no_keys_or_feedback(self):
        public = self.course.public_questions("test.intro", "a")
        self.assertEqual(len(public), 2)
        self.assertEqual(set(public[0]), {"id", "prompt", "type", "choices"})
        self.assertEqual(set(public[1]), {"id", "prompt", "type"})
        self.assertNotIn("feedback", str(public))
        self.assertNotIn("answers", str(public))

    def test_whole_set_requires_all_answers_and_rejects_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, "Complete every"):
            self.course.grade("test.intro", "a", {"q1": "2"})
        with self.assertRaisesRegex(ValueError, "unknown answer"):
            self.course.grade("test.intro", "a", {"q1": "2", "q2": "2", "q3": "2"})
        self.assertEqual(self.course.validate_answers("test.intro", "a", {"q1": " 2 "},
                                                     complete=False), {"q1": "2"})
        self.assertEqual(self.course.validate_answers("test.intro", "a", {}, complete=False), {})

    def test_choice_and_numeric_grading_preserve_separate_results(self):
        correct = self.course.grade("test.intro", "a", {"q1": "2", "q2": " 2.00e0 "})
        self.assertTrue(correct["passed"])
        self.assertEqual([item["correct"] for item in correct["items"]], [True, True])
        wrong = self.course.grade("test.intro", "a", {"q1": "1", "q2": "2"})
        self.assertFalse(wrong["passed"])
        self.assertEqual([item["correct"] for item in wrong["items"]], [False, True])
        self.assertIn("cannot bypass", wrong["items"][0]["feedback"])

    def test_text_normalization_is_declared_and_finite(self):
        self.assertTrue(self.course.grade("test.mixed", "a", {"q1": "1", "q2": " READY  "})["passed"])
        self.assertFalse(self.course.grade("test.mixed", "a", {"q1": "1", "q2": "not ready"})["passed"])
        exact = Question("q", "Prompt", "text", (" a ",), "Feedback", normalization="exact")
        self.assertEqual(normalize_answer(exact, " a "), " a ")

    def test_invalid_inputs_are_never_graded_as_wrong_answers(self):
        for answer in ("", "0", "5", "one", True, 1.0, "2\x1b[31m"):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.course.grade("test.intro", "a", {"q1": answer, "q2": "2"})
        for answer in ("NaN", "Infinity", "1+1", "2_0", "1e99999", "1e-99999", " "):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.course.grade("test.intro", "a", {"q1": "2", "q2": answer})

    def test_number_normalization_does_not_round_high_precision_answers(self):
        question = Question("q", "Number", "number", ("1",), "Feedback")
        one = "1.0000000000000000000000000000000000001"
        two = "1.0000000000000000000000000000000000002"
        self.assertEqual(normalize_answer(question, one), one)
        self.assertNotEqual(normalize_answer(question, one), normalize_answer(question, two))
        self.assertEqual(normalize_answer(question, "-0.000"), "0")
        self.assertEqual(normalize_answer(question, "10.00"), "10")

    def test_answer_size_and_control_bounds(self):
        for answer in ("a" * 257, "a\x7fb", "\x00", "\x1b[2J"):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.course.validate_answers("test.mixed", "a", {"q2": answer}, complete=False)

    def test_unknown_references_and_cycles_are_rejected(self):
        root = self.mutate(lambda raw: raw["gates"][1].update(prerequisites=["missing.gate"]))
        with self.assertRaisesRegex(ValueError, "unknown prerequisite"):
            Catalog(root)
        root = self.mutate(lambda raw: raw["gates"][0].update(prerequisites=["test.foundation"]))
        with self.assertRaisesRegex(ValueError, "cycle"):
            Catalog(root)

    def test_duplicate_ids_and_ambiguous_yaml_are_rejected(self):
        root = self.mutate(lambda raw: raw["gates"].append(copy.deepcopy(raw["gates"][0])))
        with self.assertRaisesRegex(ValueError, "duplicate educational"):
            Catalog(root)
        path = root / "course.yaml"
        path.write_text("schema: 1\nschema: 1\ngates: []\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate YAML"):
            Catalog(root)

    def test_duplicate_question_alternative_and_component_ids_are_rejected(self):
        edits = [lambda raw: raw["gates"][0]["alternatives"].append(
                    copy.deepcopy(raw["gates"][0]["alternatives"][0])),
                 lambda raw: raw["gates"][0]["alternatives"][0]["questions"].append(
                    copy.deepcopy(raw["gates"][0]["alternatives"][0]["questions"][0])),
                 lambda raw: raw["gates"][3].update(components=["test.intro", "test.intro"])]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                Catalog(self.mutate(edit))

    def test_theory_cannot_reference_a_lab_and_practical_cannot_hide_questions(self):
        root = self.mutate(lambda raw: raw["gates"][0]["alternatives"][0].update(challenge="test.mixed-copy"))
        with self.assertRaisesRegex(ValueError, "must not provision"):
            Catalog(root)
        root = self.mutate(lambda raw: raw["gates"][2]["alternatives"][0].update(
            questions=raw["gates"][0]["alternatives"][0]["questions"]))
        with self.assertRaisesRegex(ValueError, "practical has none"):
            Catalog(root)

    def test_checkpoint_observers_are_fixed_trusted_contracts(self):
        alternative = self.course.alternative("test.ordered", "a")
        self.assertEqual(alternative.checkpoints, ("rollback", "reapply"))
        for changes in ({"checkpoints": ["reapply", "rollback"]},
                        {"checkpoints": ["success-file"]},
                        {"challenge": "test.mixed-copy"}):
            root = self.mutate(lambda raw: raw["gates"][2]["alternatives"][0].update(changes))
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "checkpoint sequence"):
                Catalog(root)

    def test_digest_binds_selected_content_and_underlying_practical_fixture(self):
        digest = self.course.digest("test.intro", "a")
        root = self.mutate(lambda raw: raw["gates"][0]["alternatives"][0]["questions"][0]
                           .update(answers=["1"]))
        self.assertNotEqual(digest, Catalog(root).course.digest("test.intro", "a"))
        root = self.mutate(lambda raw: raw["gates"][1].update(prerequisites=[]))
        self.assertNotEqual(self.course.digest("test.mixed", "a"),
                            Catalog(root).course.digest("test.mixed", "a"))
        path = root / "challenges/mixed-copy.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["version"] = 2
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.assertNotEqual(self.course.digest("test.mixed", "a"),
                            Catalog(root).course.digest("test.mixed", "a"))

    def test_pool_growth_keeps_unchanged_selected_alternative_digest(self):
        root = self.mutate(lambda raw: raw["gates"][0]["alternatives"].append(
            dict(copy.deepcopy(raw["gates"][0]["alternatives"][0]), id="c")))
        self.assertEqual(self.course.digest("test.intro", "a"),
                         Catalog(root).course.digest("test.intro", "a"))

    def test_course_sidecar_does_not_change_legacy_challenge_digest(self):
        root = self.mutate(lambda raw: raw["gates"][0]["alternatives"][0]["questions"][0]
                           .update(answers=["1"]))
        self.assertEqual(self.catalog.digest("test.legacy-copy"), Catalog(root).digest("test.legacy-copy"))

    def test_course_practical_challenges_are_exclusive_and_removed_from_legacy_pools(self):
        self.assertEqual(set(self.catalog.pool(12, 61000)), {"test.legacy-copy", "test.legacy-directory"})
        self.assertEqual(self.course.challenge_ids,
                         {"test.mixed-copy", "test.mixed-directory", "test.ordered-policy"})
        root = self.mutate(lambda raw: None)
        before = Catalog(root).digest("test.mixed-copy")
        legacy_digest = Catalog(root).digest("test.legacy-copy")
        (root / "course.yaml").unlink()
        legacy = Catalog(root)
        self.assertIn("test.mixed-copy", legacy.pool(12, 61000))
        self.assertNotEqual(before, legacy.digest("test.mixed-copy"))
        self.assertEqual(legacy_digest, legacy.digest("test.legacy-copy"))

    def test_invalid_keys_and_oversized_questions_fail_preflight(self):
        for changes in ({"answers": ["9"]}, {"feedback": ""}, {"prompt": "x" * 4097},
                        {"answers": ["1", "2"]}, {"option_feedback": ["only one"]}):
            root = self.mutate(lambda raw: raw["gates"][0]["alternatives"][0]["questions"][0].update(changes))
            with self.subTest(changes=str(changes)[:80]), self.assertRaises(ValueError):
                Catalog(root)

    def test_malformed_enumeration_types_fail_clean_preflight(self):
        edits = [lambda raw: raw["gates"][0].update(format=[]),
                 lambda raw: raw["gates"][0]["alternatives"][0]["questions"][0].update(type=[]),
                 lambda raw: raw["gates"][0]["alternatives"][0]["questions"][0].update(normalization=[])]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                Catalog(self.mutate(edit))

    def test_total_feedback_must_fit_compact_commitment_result_budget(self):
        def oversized_feedback(raw):
            for question in raw["gates"][0]["alternatives"][0]["questions"]:
                question["feedback"] = "f" * 4096
        with self.assertRaisesRegex(ValueError, "7 KiB"):
            Catalog(self.mutate(oversized_feedback))


if __name__ == "__main__":
    unittest.main()
