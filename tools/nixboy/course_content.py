"""Optional educational catalog and bounded trusted grading, independent of skills.

Only ``public_questions`` is learner-facing. Keys and feedback stay in the
operator-owned catalog; the manager decides when a committed set may be graded.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from challenge_content import ContentLoader, IDENTIFIER
import yaml


MAX_COURSE_BYTES = 1024 * 1024
MAX_ANSWER_BYTES = 256
MAX_GRADE_BYTES = 7 * 1024
FAILED_FEEDBACK = "Review the indicated conceptual answers before an explicit retry."
PASSED_FEEDBACK = "All conceptual answers passed."


def _identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _text(value, maximum=4096):
    return isinstance(value, str) and 0 < len(value) <= maximum and "\0" not in value


def _identifiers(value, maximum=64):
    return isinstance(value, list) and len(value) <= maximum \
        and all(_identifier(item) for item in value) and len(set(value)) == len(value)


@dataclass(frozen=True)
class Question:
    id: str
    prompt: str
    type: str
    answers: tuple[str, ...]
    feedback: str
    choices: tuple[str, ...] = ()
    normalization: str = "space"
    option_feedback: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alternative:
    id: str
    title: str
    instructions: str
    challenge: str | None
    questions: tuple[Question, ...]
    checkpoints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Gate:
    id: str
    title: str
    format: str
    prerequisites: tuple[str, ...]
    alternatives: tuple[Alternative, ...]
    components: tuple[str, ...] = ()
    version: int = 1


def normalize_answer(question, value):
    """Normalize finite learner data without evaluation, fuzzy or prose grading."""
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_ANSWER_BYTES \
            or any((ord(char) < 32 and char not in "\t\r\n") or ord(char) == 127 for char in value):
        raise ValueError("Answer must be nonempty bounded text without control characters")
    if question.type == "choice":
        result = value.strip()
        if result not in {str(index) for index in range(1, len(question.choices) + 1)}:
            raise ValueError("Choose one numbered option")
        return result
    if question.type == "number":
        # Decimal is exact, finite and bounded before expansion. No expressions,
        # NaNs, infinities or huge exponents enter saved state or feedback.
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value.strip()):
            raise ValueError("Enter one finite number, not an expression")
        try:
            number = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError("Enter one finite number, not an expression") from exc
        if not number.is_finite() or abs(number.adjusted()) > 100 \
                or len(number.as_tuple().digits) > 100:
            raise ValueError("Number is outside the supported finite range")
        result = format(number, "f")
        if "." in result:
            result = result.rstrip("0").rstrip(".")
        return "0" if number.is_zero() else result
    if question.normalization == "exact":
        result = value
    else:
        result = " ".join(value.split())
        if question.normalization == "space-casefold":
            result = result.casefold()
    if not result:
        raise ValueError("Answer cannot be empty")
    return result


class CourseCatalog:
    """Load the optional schema-1 course sidecar, referencing trusted challenges."""

    def __init__(self, root, challenges):
        self.root = Path(root).resolve()
        self.challenges = challenges
        path = self.root / "course.yaml"
        if path.stat().st_size > MAX_COURSE_BYTES:
            raise ValueError("course definition exceeds 1 MiB")
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=ContentLoader)
        if not isinstance(raw, dict) or set(raw) != {"schema", "gates"} \
                or type(raw["schema"]) is not int or raw["schema"] != 1 \
                or not isinstance(raw["gates"], list) or not 1 <= len(raw["gates"]) <= 512:
            raise ValueError("course needs schema 1 and between one and 512 gates")
        self.gates = {}
        for data in raw["gates"]:
            gate = self._gate(data)
            if gate.id in self.gates:
                raise ValueError("duplicate educational gate id")
            self.gates[gate.id] = gate
        self._validate_graph()
        self.challenge_ids = frozenset(alternative.challenge for gate in self.gates.values()
                                       for alternative in gate.alternatives if alternative.challenge is not None)

    def _gate(self, data):
        required = {"id", "title", "format", "prerequisites"}
        optional = {"version", "components", "alternatives"}
        if not isinstance(data, dict) or not required <= set(data) or set(data) - required - optional \
                or not _identifier(data["id"]) or not _text(data["title"], 200) \
                or not isinstance(data["format"], str) \
                or data["format"] not in {"practical", "theory", "mixed", "all"} \
                or not _identifiers(data["prerequisites"]) \
                or type(data.get("version", 1)) is not int or not 1 <= data.get("version", 1) <= 2**31 - 1:
            raise ValueError("invalid educational gate fields")
        components = data.get("components", [])
        alternatives = data.get("alternatives", [])
        if not _identifiers(components) or not isinstance(alternatives, list) or len(alternatives) > 32:
            raise ValueError("invalid gate components or alternatives")
        if data["format"] == "all":
            if not components or alternatives:
                raise ValueError("all gate needs components and no alternatives")
        elif components or not alternatives:
            raise ValueError("assessable gate needs alternatives and no components")
        parsed = tuple(self._alternative(item, data["format"]) for item in alternatives)
        if len({item.id for item in parsed}) != len(parsed):
            raise ValueError("duplicate alternative id within gate")
        return Gate(data["id"], data["title"], data["format"], tuple(data["prerequisites"]),
                    parsed, tuple(components), data.get("version", 1))

    def _alternative(self, data, gate_format):
        required = {"id", "title", "instructions"}
        optional = {"challenge", "questions", "checkpoints"}
        if not isinstance(data, dict) or not required <= set(data) or set(data) - required - optional \
                or not _identifier(data["id"]) or not _text(data["title"], 200) \
                or not _text(data["instructions"], 8192):
            raise ValueError("invalid course alternative fields")
        challenge = data.get("challenge")
        if gate_format == "theory":
            if challenge is not None:
                raise ValueError("theory alternative must not provision a challenge")
        elif not _identifier(challenge):
            raise ValueError("practical or mixed alternative needs a challenge id")
        if challenge is not None:
            self.challenges.get(challenge)
        questions = data.get("questions", [])
        if not isinstance(questions, list) or len(questions) > 32 \
                or (bool(questions) != (gate_format in {"theory", "mixed"})):
            raise ValueError("theory and mixed alternatives need bounded questions; practical has none")
        parsed = tuple(self._question(item) for item in questions)
        if len({item.id for item in parsed}) != len(parsed):
            raise ValueError("duplicate question id within alternative")
        # The manager retains compact grading alongside commitment identity.
        # Reject an ungradable fixture before issuance, rather than committing
        # a healthy learner attempt that can never fit its trusted result bound.
        worst_items = []
        for question in parsed:
            feedback = question.feedback
            if question.option_feedback:
                feedback += " " + max(question.option_feedback,
                                      key=lambda item: len(json.dumps(item).encode("utf-8")))
            worst_items.append({"id": question.id, "correct": False, "feedback": feedback})
        worst_result = {"passed": False, "items": worst_items, "feedback": FAILED_FEEDBACK}
        if len(json.dumps(worst_result, sort_keys=True).encode("utf-8")) > MAX_GRADE_BYTES:
            raise ValueError("alternative grade feedback exceeds the 7 KiB compact result budget")
        checkpoints = data.get("checkpoints", [])
        if not _identifiers(checkpoints, 16) or (checkpoints and challenge is None):
            raise ValueError("invalid checkpoint sequence")
        # Only implemented checkpoint observers can be bound. Adding another
        # sequence requires an explicit trusted observer, not authored code.
        if checkpoints and (self.challenges.get(challenge).kind != "checkpoint-policy"
                            or checkpoints != ["rollback", "reapply"]):
            raise ValueError("unsupported trusted checkpoint sequence")
        return Alternative(data["id"], data["title"], data["instructions"], challenge, parsed,
                           tuple(checkpoints))

    @staticmethod
    def _question(data):
        required = {"id", "type", "prompt", "answers", "feedback"}
        optional = {"choices", "normalization", "option_feedback"}
        if not isinstance(data, dict) or not required <= set(data) or set(data) - required - optional \
                or not _identifier(data["id"]) or not _text(data["prompt"], 4096) \
                or not isinstance(data["type"], str) or data["type"] not in {"choice", "number", "text"} \
                or not isinstance(data["answers"], list) or not 1 <= len(data["answers"]) <= 16 \
                or any(not _text(answer, MAX_ANSWER_BYTES) for answer in data["answers"]) \
                or not _text(data["feedback"], 4096):
            raise ValueError("invalid bounded question fields")
        choices = data.get("choices", [])
        option_feedback = data.get("option_feedback", [])
        normalization = data.get("normalization", "space")
        if not isinstance(normalization, str) or normalization not in {"exact", "space", "space-casefold"} \
                or not isinstance(choices, list) or not isinstance(option_feedback, list):
            raise ValueError("invalid question normalization or choices")
        if data["type"] == "choice":
            if not 2 <= len(choices) <= 8 or any(not _text(item, 1024) for item in choices) \
                    or len(data["answers"]) != 1:
                raise ValueError("choice question needs two to eight options and exactly one key")
            if option_feedback and (len(option_feedback) != len(choices)
                                    or any(not _text(item, 2048) for item in option_feedback)):
                raise ValueError("option feedback must explain every choice")
        elif choices or option_feedback:
            raise ValueError("only choice questions have choices or option feedback")
        question = Question(data["id"], data["prompt"], data["type"], tuple(data["answers"]),
                            data["feedback"], tuple(choices), normalization, tuple(option_feedback))
        try:
            normalized = tuple(normalize_answer(question, answer) for answer in question.answers)
        except ValueError as exc:
            raise ValueError("invalid authored question key: " + str(exc)) from exc
        return Question(question.id, question.prompt, question.type, normalized, question.feedback,
                        question.choices, question.normalization, question.option_feedback)

    def _validate_graph(self):
        visited, active = set(), set()

        def visit(identifier):
            if identifier not in self.gates:
                raise ValueError("unknown prerequisite or component: " + identifier)
            if identifier in active:
                raise ValueError("educational prerequisite/component cycle")
            if identifier in visited:
                return
            active.add(identifier)
            gate = self.gates[identifier]
            for target in gate.prerequisites + gate.components:
                visit(target)
            active.remove(identifier)
            visited.add(identifier)

        for identifier in self.gates:
            visit(identifier)

    def get(self, identifier):
        try:
            return self.gates[identifier]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown educational gate") from exc

    def alternative(self, gate, identifier):
        for alternative in self.get(gate).alternatives:
            if alternative.id == identifier:
                return alternative
        raise ValueError("unknown course alternative")

    def is_complete(self, identifier, completed):
        gate = self.get(identifier)
        if gate.format == "all":
            return all(self.is_complete(component, completed) for component in gate.components)
        return identifier in completed

    def missing_prerequisites(self, identifier, completed):
        return tuple(item for item in self.get(identifier).prerequisites
                     if not self.is_complete(item, completed))

    def eligible(self, identifier, completed):
        return not self.missing_prerequisites(identifier, completed)

    def digest(self, gate, alternative):
        selected = self.alternative(gate, alternative)
        related = {}

        def include(identifier):
            if identifier in related:
                return
            item = self.get(identifier)
            # Bind graph structure but avoid unrelated alternatives invalidating
            # this selected assessment when the retained pool grows.
            related[identifier] = {"id": item.id, "version": item.version, "format": item.format,
                                   "prerequisites": item.prerequisites, "components": item.components}
            for target in item.prerequisites + item.components:
                include(target)

        include(gate)
        payload = {"gate": related, "alternative": asdict(selected)}
        if selected.challenge is not None:
            payload["challenge"] = self.challenges.digest(selected.challenge)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                              .encode("utf-8")).hexdigest()

    def public_questions(self, gate, alternative):
        result = []
        for question in self.alternative(gate, alternative).questions:
            item = {"id": question.id, "prompt": question.prompt, "type": question.type}
            if question.choices:
                item["choices"] = list(question.choices)
            result.append(item)
        return result

    def validate_answers(self, gate, alternative, answers, complete=True):
        questions = {item.id: item for item in self.alternative(gate, alternative).questions}
        if not isinstance(answers, dict) or set(answers) - set(questions):
            raise ValueError("unknown answer id")
        if complete and set(answers) != set(questions):
            raise ValueError("Complete every answer before committing the whole set")
        return {identifier: normalize_answer(questions[identifier], value)
                for identifier, value in answers.items()}

    def grade(self, gate, alternative, answers):
        normalized = self.validate_answers(gate, alternative, answers, complete=True)
        items = []
        for question in self.alternative(gate, alternative).questions:
            correct = normalized[question.id] in question.answers
            feedback = question.feedback
            if question.option_feedback:
                feedback += " " + question.option_feedback[int(normalized[question.id]) - 1]
            items.append({"id": question.id, "correct": correct, "feedback": feedback})
        passed = bool(items) and all(item["correct"] for item in items)
        return {"passed": passed, "items": items,
                "feedback": PASSED_FEEDBACK if passed else FAILED_FEEDBACK}
