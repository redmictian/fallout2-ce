"""Bounded current assessments and canonical project stages in the manager."""
import json
import secrets
import threading
import time


def bounded_evidence(value, maximum=8192):
    encoded = json.dumps(value, sort_keys=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("Trusted educational evidence exceeds its bound")
    return json.loads(encoded)


class CourseProgress:
    def _course(self):
        course = getattr(self.catalog, "course", None)
        if course is None:
            raise ValueError("This catalog has no assessments")
        return course

    def _project_eligible(self, profile, gate):
        if not gate.project:
            return True
        current = profile.get("project")
        expected = current["next_stage"] if current and current["id"] == gate.project["id"] else 1
        return gate.project["stage"] == expected

    def course_status(self, identity):
        with self.lock:
            profile = self._profile(identity)
            project = profile.get("project")
            return [{"id": gate.id, "title": gate.title, "format": gate.format,
                     "complete": bool(gate.project and project and project["id"] == gate.project["id"]
                                      and project["next_stage"] > gate.project["stage"]),
                     "eligible": self._project_eligible(profile, gate) and bool(
                         profile["assessment_queues"].get(gate.id, {"pending": [None]})["pending"])}
                    for gate in self._course().gates.values()]

    def _assessment_pool(self, profile, gate):
        authored = [alt.id for alt in gate.alternatives]
        stored = profile["assessment_queues"].get(gate.id)
        if stored is None:
            pool = list(authored)
            secrets.SystemRandom().shuffle(pool)
            stored = profile["assessment_queues"][gate.id] = {"authored": authored, "pending": pool, "exposed": []}
        else:
            stored["pending"] = [item for item in stored["pending"] if item in authored]
            stored["exposed"] = [item for item in stored["exposed"] if item in authored]
            added = [item for item in authored if item not in stored["authored"]]
            secrets.SystemRandom().shuffle(added)
            stored["pending"].extend(added)
            stored["authored"] = authored
        if not stored["pending"]:
            raise ValueError("All alternatives for this assessment were completed in the current run")
        return stored

    def select_course(self, identity, gate_id):
        with self._operation(identity), self.lock:
            if self.closed:
                raise RuntimeError("Training service is stopping")
            profile = self._profile(identity)
            if profile.get("attempt"):
                raise ValueError("Resume, retry or abandon the active exercise first")
            course = self._course()
            gate = course.get(gate_id)
            self._pending_guard(profile)
            if not self._project_eligible(profile, gate):
                raise ValueError("Complete the previous canonical project stage in the current run first")
            self._perk_eligibility(identity, gate.reward)
            queue = self._assessment_pool(profile, gate)
            seen = queue["exposed"]
            alternative_id = next((item for item in queue["pending"] if item not in seen), queue["pending"][0])
            alt = course.alternative(gate_id, alternative_id)
            seed = secrets.randbits(63)
            spec = self.catalog.get(alt.challenge) if alt.challenge else None
            attempt = {"id": secrets.token_hex(16), "course_gate": gate_id,
                       "alternative": alt.id, "course_digest": course.digest(gate_id, alt.id),
                       "format": gate.format, "exposed_retry": alt.id in seen,
                       "challenge": alt.challenge, "skill": spec.skill if spec else None,
                       "level": spec.level if spec else None, "seed": seed, "generation": 0,
                       "environment_kind": spec.kind if spec else "theory",
                       "challenge_version": spec.version if spec else 1,
                       "content_digest": self.catalog.digest(spec.id) if spec else None,
                       "reward_points": 0, "params": self.catalog.instantiate(spec.id, seed) if spec else {},
                       "mode": "course", "status": "selected", "updated": time.time(),
                       "answers": {}, "conceptual": None, "practical": None, "checkpoints": []}
            if gate.project:
                current = profile.get("project")
                if not current or current["id"] != gate.project["id"]:
                    profile["project"] = {"id": gate.project["id"], "next_stage": 1}
                from nginx_env import preparation_identity
                attempt["project"] = dict(gate.project)
                attempt["preparation_identity"] = preparation_identity(gate.project["preparation"])
            profile["attempt"] = attempt
            self._save()
            return bounded_evidence(attempt, 65536)

    def _course_attempt(self, identity):
        attempt = self._profile(identity).get("attempt")
        if not attempt or not attempt.get("course_gate"):
            raise ValueError("No active educational assessment")
        self._check_content(attempt)
        if attempt.get("status") == "provider_lost":
            raise ValueError(attempt.get("facility_error", "Facility interrupted; F7 reissues"))
        return attempt

    def questions(self, identity):
        with self.lock:
            attempt = self._course_attempt(identity)
            return {"questions": self._course().public_questions(attempt["course_gate"], attempt["alternative"]),
                    "answers": dict(attempt["answers"]), "committed": bool(attempt.get("conceptual")),
                    "feedback": attempt.get("conceptual")}

    def draft_answer(self, identity, question_id, value):
        with self._operation(identity), self.lock:
            attempt = self._course_attempt(identity)
            if attempt.get("conceptual"):
                raise ValueError("Answers are committed; use explicit T retry for a fresh attempt")
            answers = dict(attempt["answers"])
            answers[question_id] = value
            normalized = self._course().validate_answers(attempt["course_gate"], attempt["alternative"], answers,
                                                         complete=False)
            attempt["answers"] = normalized
            self._save()
            return {"status": "draft", "feedback": "Draft saved; no grading or feedback before COMMIT."}

    def commit_answers(self, identity):
        with self._operation(identity), self.lock:
            attempt = self._course_attempt(identity)
            if attempt.get("conceptual"):
                return (bounded_evidence(attempt["conceptual"]) if attempt["conceptual"].get("graded") else
                        self._grade_committed(identity, attempt))
            course = self._course()
            gate, alt = attempt["course_gate"], attempt["alternative"]
            if not course.public_questions(gate, alt):
                raise ValueError("This practical-only gate has no conceptual questions")
            answers = course.validate_answers(gate, alt, attempt["answers"], complete=True)
            # Persist commitment BEFORE requesting/revealing trusted grading. A
            # crash here remains committed and is graded on subsequent access.
            attempt["answers"] = answers
            attempt["conceptual"] = {"committed": True, "graded": False, "passed": False,
                                      "attempt": attempt["id"], "generation": attempt["generation"],
                                      "content": attempt["course_digest"], "at": time.time()}
            seen = self._assessment_pool(self._profile(identity), self._course().get(gate))["exposed"]
            if alt not in seen:
                seen.append(alt)
            self._save()
            return self._grade_committed(identity, attempt)

    def _grade_committed(self, identity, attempt):
        result = self._course().grade(attempt["course_gate"], attempt["alternative"], attempt["answers"])
        attempt["conceptual"].update(bounded_evidence(result))
        attempt["conceptual"].update(committed=True, graded=True)
        self._save()
        return bounded_evidence(attempt["conceptual"])

    def course_feedback(self, identity):
        with self._operation(identity), self.lock:
            attempt = self._course_attempt(identity)
            if not attempt.get("conceptual"):
                raise ValueError("Feedback is withheld until the complete set is committed")
            if not attempt["conceptual"].get("graded"):
                self._grade_committed(identity, attempt)
            return bounded_evidence(attempt["conceptual"])

    def checkpoint(self, identity, name, expected=None):
        """Observe the next checkpoint; accept no learner-supplied evidence.

        Optional expected binding is useful to reject delayed UI/test requests;
        it is never accepted as proof. Serial socket requests carry only a name.
        """
        with self._operation(identity):
            with self.lock:
                attempt = self._course_attempt(identity)
                binding = {"attempt": attempt["id"], "generation": attempt["generation"],
                           "content": attempt["course_digest"]}
                if expected is not None and expected != binding:
                    raise ValueError("Stale or cross-attempt checkpoint request")
                names = self._course().alternative(attempt["course_gate"], attempt["alternative"]).checkpoints
                index = len(attempt["checkpoints"])
                if index >= len(names) or name != names[index]:
                    raise ValueError("Out-of-order checkpoint; expected " + (names[index] if index < len(names) else "no further checkpoint"))
                environment = self.environments.get(identity)
                if environment is None:
                    raise ValueError("Resume the prepared environment before checkpointing")
                cancel = threading.Event()
                self.cancel_events[identity] = cancel
            try:
                result = environment.checkpoint(name)
                if result.get("status") not in ("passed", "incomplete", "error"):
                    raise ValueError("Invalid trusted checkpoint result")
                evidence = bounded_evidence(result.get("evidence", {}), 2048)
            except Exception as exc:
                result = {"status": "error", "feedback": "Checkpoint facility error: " + str(exc)[:200]}
            with self.lock:
                self.cancel_events.pop(identity, None)
                if self.closed or cancel.is_set() or self._profile(identity).get("attempt") is not attempt \
                        or attempt.get("status") == "provider_lost":
                    return {"status": "error", "feedback": "Checkpoint interrupted; no observation was accepted."}
                self._check_content(attempt)
                if result["status"] == "passed":
                    attempt["checkpoints"].append({**binding, "name": name, "index": index,
                                                   "at": time.time(), "evidence": evidence})
                elif result.get("facility_failure"):
                    self._provider_stopped(attempt)
                self._save()
            return result

    def retry_course(self, identity):
        with self.lock:
            profile = self._profile(identity)
            attempt = profile.get("attempt")
            if attempt and not attempt.get("course_gate"):
                raise ValueError("Resume or explicitly abandon the active legacy exercise before educational retry")
            if attempt and attempt.get("course_gate"):
                if not attempt.get("conceptual"):
                    raise ValueError("Before commitment use drafts, reset or abandon; no reassessment is needed")
                gate = attempt["course_gate"]
            else:
                gate = profile.get("last_assessment")
                if not gate:
                    raise ValueError("No current-run assessment to retry")
        self.abandon(identity)
        return self.select_course(identity, gate)

    def _course_check(self, identity, cancel=None, progress=None):
        with self._operation(identity):
            with self.lock:
                try:
                    attempt = self._course_attempt(identity)
                except ValueError as exc:
                    return {"status": "error", "feedback": str(exc)}
                fmt = attempt["format"]
                conceptual = attempt.get("conceptual")
                if fmt != "practical" and not conceptual:
                    # P submission can be observed without finalizing; questions
                    # and checkpoints remain nonfinal regardless of the outcome.
                    if fmt == "theory":
                        return {"status": "pending", "feedback": "Commit the complete question set first; Q opens questions."}
                if conceptual and not conceptual.get("graded"):
                    conceptual = self._grade_committed(identity, attempt)
                environment = self.environments.get(identity)
                if fmt != "theory" and not environment:
                    return {"status": "error", "feedback": "Resume the practical environment before checking."}
                if attempt.get("preparation_identity") and getattr(environment, "preparation_identity", None) != attempt["preparation_identity"]:
                    self._provider_stopped(attempt)
                    self._save()
                    return {"status": "error", "facility_failure": True, "feedback": "Canonical project preparation identity mismatch; reissue."}
                cancel = cancel or threading.Event()
                self.cancel_events[identity] = cancel
            try:
                practical = {"status": "passed", "evidence": {"theory_only": True}}
                if fmt != "theory":
                    practical = environment.check()
                if practical.get("status") not in ("passed", "incomplete", "error"):
                    raise ValueError("Invalid trusted practical result")
                practical = bounded_evidence(practical, 4096)
            except Exception as exc:
                practical = {"status": "error", "feedback": "Practical facility error: " + str(exc)[:200]}
            finally:
                with self.lock:
                    self.cancel_events.pop(identity, None)
            with self.lock:
                if self.closed or cancel.is_set() or self._profile(identity).get("attempt") is not attempt \
                        or attempt.get("status") == "provider_lost":
                    return {"status": "error", "feedback": "Check cancelled; no learner result recorded."}
                try:
                    self._check_content(attempt)
                except ValueError as exc:
                    return {"status": "error", "feedback": str(exc)}
                if practical["status"] == "error":
                    if practical.get("facility_failure"):
                        self._provider_stopped(attempt)
                    self._save()
                    return practical
                binding = {"attempt": attempt["id"], "generation": attempt["generation"],
                           "content": attempt["course_digest"]}
                attempt["practical"] = {**binding, **practical, "at": time.time()}
                if fmt == "mixed" and not conceptual:
                    self._save()
                    return {"status": "pending", "feedback": "Practical observed; conceptual set is still uncommitted. Q opens questions; no completion or reward."}
                alt = self._course().alternative(attempt["course_gate"], attempt["alternative"])
                checkpoints_ok = len(attempt["checkpoints"]) == len(alt.checkpoints) and all(
                    item.get("name") == alt.checkpoints[i] and all(item.get(key) == value for key, value in binding.items())
                    for i, item in enumerate(attempt["checkpoints"]))
                if not checkpoints_ok:
                    self._save()
                    return {"status": "pending", "feedback": "Required ordered checkpoints are not complete; final state alone is insufficient."}
                conceptual_ok = fmt == "practical" or (conceptual.get("passed") and all(
                    conceptual.get(key) == value for key, value in binding.items()))
                passed = practical["status"] == "passed" and bool(conceptual_ok)
                profile = self._profile(identity)
                before_completion = json.loads(json.dumps(profile))
                perk_rewarded = False
                if passed:
                    try:
                        perk_rewarded = self._earn_perk(identity, self._course().get(attempt["course_gate"]), attempt)
                    except ValueError as exc:
                        return {"status": "error", "feedback": str(exc) + "; attempt preserved without reward."}
                gate = self._course().get(attempt["course_gate"])
                queue = self._assessment_pool(profile, gate)
                alternative = attempt["alternative"]
                queue["pending"].remove(alternative)
                if not passed:
                    queue["pending"].append(alternative)
                if passed and gate.project:
                    profile["project"] = {"id": gate.project["id"], "next_stage": gate.project["stage"] + 1}
                profile["last_assessment"] = gate.id
                profile["attempt"] = None
                try:
                    self._save()
                except Exception:
                    profile.clear()
                    profile.update(before_completion)
                    raise
            self._remove_environment(identity)
            return {"status": "passed" if passed else "incomplete", "rewarded": False, "perk_rewarded": perk_rewarded, "assessment": True,
                    "feedback": ("Assessment complete; current passive perk reward pending." if perk_rewarded else "Assessment complete; no game reward.") if passed else
                    "Required practical AND conceptual criteria did not both pass. Use T for explicit reassessment."}
