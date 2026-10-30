#!/usr/bin/env python3
"""Opt-in NB-01--05 acceptance using a real bridge, Docker and disposable profiles.

No game process, player save or production training directory is used. Manager
API calls exercise backend rules; learner filesystem changes use bridge PTY INPUT.
This does not establish actual game question entry/rendering or save behavior.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import threading
import time

from bridge import BridgeServer, local_docker_client
from challenge_content import Catalog
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace
from protocol import TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET
from smoke_training import Probe, ShellSolution
from training import AttemptManager


ROOT = Path(__file__).resolve().parent


def save(directory, name, value):
    (directory / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def rejected(operation):
    try:
        operation()
    except (ValueError, RuntimeError) as exc:
        return {"rejected": True, "type": type(exc).__name__, "message": str(exc)}
    raise AssertionError("invalid operation was accepted")


class AssessmentProbe(Probe):
    def hub(self):
        self.action(TRAIN_HUB)
        self.until_output(b"Choose a number")

    def launch(self, gate):
        self.hub()
        self.manager.select_course(self.identity, gate)
        attempt = self.manager.snapshot(self.identity)["attempt"]
        if attempt.get("challenge") is not None:
            self.input(b"r\r")
            self.until_output(b"challenge:~$ ")
        return attempt

    def resume(self):
        self.input(b"r\r")
        self.until_output(b"challenge:~$ ")

    def script(self, text):
        code, output, error = self.command(["/bin/bash", "--noprofile", "--norc", "-c", text])
        if code:
            raise AssertionError({"command": text, "code": code, "output": output, "error": error})
        return output


def start_bridge(state, catalog, profile=None):
    manager = AttemptManager(state, local_docker_client, catalog=catalog)
    token = secrets.token_hex(32).encode("ascii")
    server = BridgeServer(0, token, "fallout2-nixboy:dev", client_factory=local_docker_client,
                          training_manager=manager)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    probe = AssessmentProbe(server, token, manager)
    if profile is not None:
        probe.profile = profile
        probe.identity = profile.hex()
    probe.connect()
    return manager, server, thread, probe


def close_bridge(instance):
    manager, server, thread, probe = instance
    try:
        probe.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def resources(state):
    client = local_docker_client()
    try:
        labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state)}"]
        return {
            "containers": [item["Id"] for item in client.api.containers(all=True, filters={"label": labels})],
            "networks": [item["Id"] for item in client.api.networks(filters={"label": labels})],
            "volumes": [item["Name"] for item in (client.api.volumes(filters={"label": labels}).get("Volumes") or [])],
        }
    finally:
        client.close()


def active(manager, identity):
    return manager.snapshot(identity)["attempt"]


def assert_no_course_reward(manager, probe):
    assert not manager.snapshot(probe.identity)["receipts"]
    assert not manager.pending_rewards(probe.identity, probe.progress())


def answers(manager, identity, wrong=False):
    attempt = active(manager, identity)
    if attempt["course_gate"] == "test.intro":
        values = {"a": {"q1": "2", "q2": "2.00"}, "b": {"q1": "1", "q2": "3.00"}}[attempt["alternative"]]
    else:
        values = {"q1": "1", "q2": "  ReAdY  "}
        if wrong:
            values["q1"] = "2"
    for identifier, value in values.items():
        manager.draft_answer(identity, identifier, value)
    return manager.commit_answers(identity)


def binding(attempt):
    return {"attempt": attempt["id"], "generation": attempt["generation"], "content": attempt["course_digest"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="new private disposable evidence/state directory")
    parser.add_argument("--content-dir", type=Path, default=ROOT / "content-assessment-test")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required to create disposable real Docker environments")
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("output directory must be new or empty; never use player training state")
    out.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(args.content_dir)
    loader = importlib.util.spec_from_file_location("training_reference", ROOT / "tests/training_reference.py")
    reference = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(reference)
    instances, states, checks = [], [], []
    started = time.monotonic()
    summary = {"version": 1, "passed": False, "scope": "real bridge/Docker; backend manager rules and learner PTY changes; no game/UI/save claim", "checks": checks,
               "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in ("training.py", "course_content.py", "course_progress.py", "challenge_env.py",
                                              "challenge_content.py", "smoke_assessment_training.py")}}

    def instance(name, selected_catalog=catalog, profile=None):
        state = out / name / "state"
        state.parent.mkdir(parents=True, exist_ok=True)
        current = start_bridge(state, selected_catalog, profile)
        instances.append(current)
        states.append(state)
        return current

    try:
        inspector = local_docker_client()
        try:
            info = inspector.api.version()
            summary["docker"] = {key: info.get(key) for key in
                                 ("Version", "ApiVersion", "KernelVersion", "OperatingSystem", "Arch")}
        finally:
            inspector.close()
        main_instance = instance("course")
        manager, _, _, probe = main_instance
        identity = probe.identity
        original_skills = dict(probe.skill_values)
        rejected(lambda: manager.select_course(identity, "test.mixed"))
        probe.skill_values = dict.fromkeys(range(18), 300)
        manager.observe(identity, {}, probe.context())
        rejected(lambda: manager.select_course(identity, "test.mixed"))
        probe.skill_values = original_skills
        manager.observe(identity, {}, probe.context())
        checks.append("explicit-prerequisite-high-skill-cannot-bypass")

        probe.input(b"c\r")
        probe.until_output(b"Choose a gate")
        probe.input(b"1\r")
        probe.until_output(b"COMMIT")
        theory = active(manager, identity)
        assert not any(resources(states[0]).values()), "theory created Docker resources"
        before = manager.snapshot(identity)
        first_answer = "2" if theory["alternative"] == "a" else "1"
        probe.input(("q1 " + first_answer + "\r").encode("ascii"))
        probe.until_output(b"Draft saved")
        probe.input(b"commit\r")
        probe.until_output(b"Complete every answer")
        invalid = rejected(lambda: manager.commit_answers(identity))
        early_feedback = rejected(lambda: manager.course_feedback(identity))
        after = manager.snapshot(identity)
        assert after["education"]["queues"] == before["education"]["queues"]
        assert active(manager, identity)["id"] == theory["id"]
        public = manager.questions(identity)
        assert "explanation" not in json.dumps(public).lower()
        save(out, "theory-incomplete-public", {"rejection": invalid, "premature_feedback": early_feedback,
                                             "public": public, "attempt": active(manager, identity),
                                             "terminal": probe.output.decode("utf-8", "replace")})
        checks.append("theory-no-environment-incomplete-set-does-not-grade-or-finalize")

        second_answer = "2.00" if theory["alternative"] == "a" else "3.00"
        probe.input(("q2 " + second_answer + "\r").encode("ascii"))
        probe.until_output(b"Draft saved")
        probe.input(b"n\r")
        probe.until_output(b"Question 2/2")
        probe.input(b"b\r")
        probe.until_output(b"Question 1/2")
        probe.input(b"commit\r")
        probe.until_output(b"Committed / conceptual passed")
        committed = manager.course_feedback(identity)
        assert committed["passed"]
        probe.close()
        probe.connect()
        assert active(manager, identity)["conceptual"]["committed"]
        probe.input(b"q\r")
        probe.until_output(b"Committed / conceptual passed")
        rejected(lambda: manager.draft_answer(identity, "q1", "4"))
        assert not manager.snapshot(identity)["education"]["completions"]
        result = manager.check(identity)
        assert result["status"] == "passed", result
        assert "test.intro" in manager.snapshot(identity)["education"]["completions"]
        assert not next(item for item in manager.course_status(identity) if item["id"] == "test.foundation")["complete"]
        assert_no_course_reward(manager, probe)
        assert probe.skill_values == original_skills
        manager.observe(identity, {}, probe.context())
        assert "test.intro" in manager.snapshot(identity)["education"]["completions"]
        checks.append("committed-reconnect-immutable-final-theory-completion-no-skill-change")
        checks.append("socket-course-selection-draft-navigation-whole-set-commit-and-feedback")
        checks.append("older-same-profile-context-does-not-erase-educational-progress")

        mixed = probe.launch("test.mixed")
        reference.solve(ShellSolution(probe, mixed, catalog))
        probe.hub()
        only_p = manager.check(identity)
        assert only_p["status"] != "passed" and active(manager, identity)["id"] == mixed["id"], only_p
        assert "test.mixed" not in manager.snapshot(identity)["education"]["completions"]
        assert_no_course_reward(manager, probe)
        answers(manager, identity)
        probe.resume()
        if catalog.get(mixed["challenge"]).kind == "copy-file":
            probe.script("rm -f ~/lab/delivery/manifest.txt")
        else:
            probe.script("rmdir ~/lab/assessment-dir-b/ready")
        probe.hub()
        changed = manager.check(identity)
        assert changed["status"] == "incomplete", changed
        assert active(manager, identity) is None
        checks.append("practical-only-does-not-complete-and-mutable-outcome-is-rechecked")

        manager.retry_course(identity)
        mixed = active(manager, identity)
        probe.resume()
        answers(manager, identity)
        probe.hub()
        only_k = manager.check(identity)
        assert only_k["status"] == "incomplete", only_k
        assert "test.mixed" not in manager.snapshot(identity)["education"]["completions"]
        assert_no_course_reward(manager, probe)
        checks.append("conceptual-only-does-not-complete-or-award")

        manager.retry_course(identity)
        mixed = active(manager, identity)
        assert mixed.get("exposed_retry"), mixed
        probe.resume()
        reference.solve(ShellSolution(probe, mixed, catalog))
        assert not answers(manager, identity, wrong=True)["passed"]
        probe.hub()
        assert manager.check(identity)["status"] == "incomplete"
        assert "test.mixed" not in manager.snapshot(identity)["education"]["completions"]
        assert_no_course_reward(manager, probe)
        checks.append("valid-practical-with-failed-conceptual-set-does-not-complete")

        manager.retry_course(identity)
        mixed = active(manager, identity)
        assert mixed.get("exposed_retry"), mixed
        probe.resume()
        reference.solve(ShellSolution(probe, mixed, catalog))
        assert answers(manager, identity)["passed"]
        probe.hub()
        assert manager.check(identity)["status"] == "passed"
        assert "test.mixed" in manager.snapshot(identity)["education"]["completions"]
        assert next(item for item in manager.course_status(identity) if item["id"] == "test.foundation")["complete"]
        assert_no_course_reward(manager, probe)
        checks.append("explicit-retry-prefers-unseen-then-discloses-reused-equivalent-alternative")
        checks.append("mixed-p-and-k-success-completes-without-reward")

        ordered = probe.launch("test.ordered")
        environment = manager.environments[identity]
        inspector = local_docker_client()
        try:
            info = inspector.api.inspect_container(environment.container_id)
            summary["ordinary_runner"] = {"image": info["Config"]["Image"], "image_id": info["Image"],
                                          "user": info["Config"]["User"],
                                          "host_config": {key: info["HostConfig"].get(key) for key in
                                                          ("ReadonlyRootfs", "NetworkMode", "CapDrop", "SecurityOpt", "Memory", "NanoCpus", "PidsLimit")}}
        finally:
            inspector.close()
        ready = environment.health()
        assert ready["status"] == "ready", ready
        worker = ready["evidence"]["worker"]
        probe.script("printf 'kept\\n' > ~/assessment-witness; printf '%s\\n' '{\"rollback\":\"passed\",\"reapply\":\"passed\"}' > ~/lab/checkpoint-results.json")
        probe.hub()
        final_only = manager.check(identity)
        assert final_only["status"] == "pending", final_only
        rejected(lambda: manager.checkpoint(identity, "reapply"))
        wrong_state = manager.checkpoint(identity, "rollback")
        assert wrong_state["status"] == "incomplete", wrong_state
        assert not active(manager, identity).get("checkpoints")
        probe.close()
        # An ordinary break must not expire an issued attempt, even beyond the
        # old 24h boundary. No real waiting or host-clock change is involved.
        with manager.lock:
            manager.data["profiles"][identity]["updated"] = time.time() - 7 * 24 * 60 * 60
        manager.expire()
        assert manager.environments[identity].container_id == environment.container_id
        probe.connect()
        probe.resume()
        probe.script("test -f ~/assessment-witness")
        after_worker = environment.health()["evidence"]["worker"]
        assert (worker["pid"], worker["start"]) == (after_worker["pid"], after_worker["start"])
        assert after_worker["ticks"] > worker["ticks"]
        checks.append("correct-final-file-alone-and-out-of-order-checkpoint-do-not-pass")
        checks.append("learner-written-success-claims-and-wrong-actual-checkpoint-state-do-not-pass")
        checks.append("ordinary-break-detach-reopen-preserves-worker-identity-and-learner-files")

        probe.script("cp ~/lab/old-policy.conf ~/lab/policy.conf")
        probe.hub()
        first = manager.checkpoint(identity, "rollback", expected=binding(ordered))
        assert first["status"] == "passed", first
        assert len(active(manager, identity)["checkpoints"]) == 1
        stale_binding = binding(ordered)
        manager.reset(identity)
        assert not active(manager, identity).get("checkpoints")
        rejected(lambda: manager.checkpoint(identity, "rollback", expected=stale_binding))
        forged = dict(binding(active(manager, identity)), attempt="0" * 32)
        rejected(lambda: manager.checkpoint(identity, "rollback", expected=forged))
        checks.append("reset-discards-checkpoints-and-stale-cross-attempt-bindings-rejected")
        probe.resume()
        probe.script("cp ~/lab/old-policy.conf ~/lab/policy.conf")
        probe.hub()
        assert manager.checkpoint(identity, "rollback")["status"] == "passed"
        probe.resume()
        probe.script("cp ~/lab/new-policy.conf ~/lab/policy.conf")
        probe.hub()
        assert manager.checkpoint(identity, "reapply")["status"] == "passed"
        assert manager.check(identity)["status"] == "passed"
        course_state = manager.snapshot(identity)
        assert "test.ordered" in course_state["education"]["completions"]
        assert_no_course_reward(manager, probe)
        save(out, "course-completion", course_state)
        save(out, "worker-continuity", {"before": worker, "after": after_worker})
        checks.append("real-rollback-observation-reapply-observation-completes-with-retained-evidence")

        # Separate profile: actual stopped worker is a facility failure. The
        # host-only fixture record supplies its pinned PID; learner INPUT cannot.
        fault_instance = instance("facility")
        fm, _, _, fp = fault_instance
        fp.launch("test.intro")
        answers(fm, fp.identity)
        assert fm.check(fp.identity)["status"] == "passed"
        fault_attempt = fp.launch("test.ordered")
        fault_environment = fm.environments[fp.identity]
        fp.hub()
        before = fm.snapshot(fp.identity)
        status, _, error = fault_environment._exec("workstation", ["/bin/kill", "-TERM", str(fault_environment.worker["pid"])], user="0:0")
        assert status == 0, error
        failed = fm.check(fp.identity)
        after = fm.snapshot(fp.identity)
        assert failed["status"] == "error" and failed.get("facility_failure"), failed
        assert after["education"]["queues"] == before["education"]["queues"]
        assert active(fm, fp.identity)["id"] == fault_attempt["id"]
        assert_no_course_reward(fm, fp)
        save(out, "worker-facility-failure", {"result": failed, "profile": after})
        checks.append("real-prepared-worker-loss-is-facility-failure-without-rotation-or-award")

        # Establish a healthy new generation before interrupting the provider;
        # an already-failed worker would not independently exercise that boundary.
        fm.reset(fp.identity)
        assert fm.environments[fp.identity].health()["status"] == "ready"
        before_provider = fm.snapshot(fp.identity)
        profile_bytes = fp.profile
        close_bridge(fault_instance)
        instances.remove(fault_instance)
        replacement = start_bridge(states[-1], catalog, profile_bytes)
        instances.append(replacement)
        rm, _, _, rp = replacement
        lost = active(rm, rp.identity)
        assert lost["status"] == "provider_lost", lost
        assert lost["generation"] == before_provider["attempt"]["generation"]
        assert rm.snapshot(rp.identity)["education"]["queues"] == before_provider["education"]["queues"]
        rejected(lambda: rm.prepare(rp.identity))
        assert "test.intro" in rm.snapshot(rp.identity)["education"]["completions"]
        rm.reset(rp.identity)
        assert active(rm, rp.identity)["generation"] > before_provider["attempt"]["generation"]
        assert_no_course_reward(rm, rp)
        save(out, "provider-interruption", {"healthy_before": before_provider,
                                            "lost_after_restart": lost,
                                            "reissued": active(rm, rp.identity)})
        checks.append("provider-restart-requires-explicit-reissue-and-preserves-earned-education")

        # Unchanged default legacy catalog and existing solver, actual socket
        # assessment/practice. Game reward application is deliberately not claimed.
        legacy = instance("legacy", Catalog(ROOT / "content"))
        lm, _, _, lp = legacy
        for practice in (False, True):
            attempt = lp.start(3, practice=practice)
            reference.solve(ShellSolution(lp, attempt, lm.catalog))
            lp.action(TRAIN_CHECK)
            lp.until_output(b"Practice completed; no reward" if practice else b"Skill reward earned")
            if not practice:
                lp.accept(lp.reward())
                lp.until_output(b"completed 1/")
        assert len(lm.snapshot(lp.identity)["receipts"]) == 1
        checks.append("legacy-skill-band-assessment-practice-and-existing-receipt-path")
        summary.update(passed=True, educational_profile=identity)
    except Exception as exc:
        summary["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        for current in reversed(instances):
            try:
                close_bridge(current)
            except Exception as exc:
                summary.setdefault("cleanup_errors", []).append(type(exc).__name__ + ": " + str(exc))
        cleanup = {str(state.relative_to(out)): resources(state) for state in states}
        summary["cleanup"] = cleanup
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save(out, "summary", summary)
        assert not any(any(items.values()) for items in cleanup.values()), cleanup
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
