"""Opt-in clean-candidate backend checks through real Docker/manager facilities.

This developer tool never runs a game or claims a real character effect. It uses
new disposable runtime IDs/state, real prepared files/guests, trusted validators
and the normal pending/ACK contract. It is not part of the player package.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import shlex
import time

from bridge import local_docker_client
from challenge_content import Catalog
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace, cleanup_environments
from protocol import SKILL_NAMES
from training import AttemptManager

HERE = Path(__file__).resolve().parent


def context(identity, science=10, toughness=1):
    values = {"training.version": 5, "perk.training.enabled": 1,
              "perk.toughness": toughness, "perk.toughness.max": 4}
    values.update({"skill."+name: 10 for name in SKILL_NAMES})
    values["skill.science"] = science
    for index in range(4):
        values[f"training.profile.{index}"] = int.from_bytes(
            bytes.fromhex(identity)[index*4:index*4+4], "big", signed=True)
    return values


def rejection(operation):
    try:
        operation()
    except (ValueError, RuntimeError):
        return
    raise AssertionError("invalid selection/submission was accepted")


def observed(manager, identity, claimed=None, perks=None, **values):
    manager.observe(identity, claimed or {}, context(identity, **values))
    manager.observe_perks(identity, perks or {})


def command(environment, argv):
    code, output, error = environment._exec("workstation", argv)
    assert code == 0, {"command": argv, "code": code, "output": output, "error": error}
    return output


def solve(manager, identity):
    attempt = manager.snapshot(identity)["attempt"]
    kind = manager.catalog.get(attempt["challenge"]).kind
    if kind == "copy-file":
        name = attempt["params"]["name"]
        argv = ["cp", "/home/nixboy/lab/incoming/"+name+".txt", "/home/nixboy/lab/delivery/manifest.txt"]
    elif kind == "make-directory":
        name = attempt["params"]["name"]
        argv = ["mkdir", "-p", "/home/nixboy/lab/"+name+"/ready"]
    else:
        loader = importlib.util.spec_from_file_location("clean_reference", HERE/"tests/training_reference.py")
        reference = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(reference)
        reference.solve(manager.environments[identity])
        return
    command(manager.environments[identity], argv)


def answer(manager, identity):
    attempt = manager.snapshot(identity)["attempt"]
    alternative = manager.catalog.course.alternative(attempt["course_gate"], attempt["alternative"])
    # Trusted developer solution; keys never enter learner files or wiki exports.
    for question in alternative.questions:
        manager.draft_answer(identity, question.id, question.answers[0])
    result = manager.commit_answers(identity)
    assert result["passed"], result


def resources(state):
    client = local_docker_client()
    labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state)}"]
    try:
        return {"containers": [item["Id"] for item in client.api.containers(all=True, filters={"label": labels})],
                "networks": [item["Id"] for item in client.api.networks(filters={"label": labels})],
                "volumes": [item["Name"] for item in client.api.volumes(filters={"label": labels}).get("Volumes") or []]}
    finally:
        client.close()


def ordinary(output):
    state = output/"ordinary-state"
    manager = AttemptManager(state, local_docker_client, Catalog(HERE/"content"))
    checks = []
    identity = secrets.token_hex(16)
    try:
        observed(manager, identity)
        failed = manager.select(identity, 12, 1)
        manager.prepare(identity)
        result = manager.check(identity)
        assert result["status"] == "incomplete" and not manager.pending_rewards(identity, {}), result
        selected = manager.select(identity, 12, 1)
        assert selected["challenge"] != failed["challenge"]
        checks.append("ordinary-failed-alternative-is-deferred-without-reward")
        manager.prepare(identity)
        solve(manager, identity)
        result = manager.check(identity)
        assert result["status"] == "passed" and result["rewarded"], result
        first = manager.pending_rewards(identity, {})
        assert len(first) == 1 and manager.pending_rewards(identity, {}) == first
        skill, level, points, receipt = first[0]
        manager.acknowledge(identity, skill, level, points, receipt, True)
        manager.acknowledge(identity, skill, level, points, receipt, True)
        assert not manager.pending_rewards(identity, {})
        claims = {f"{skill}:{level}": {"receipt": receipt, "points": points}}
        observed(manager, identity, claims, science=10+points)
        rejection(lambda: manager.select(identity, skill, level))
        checks.append("one-trusted-skill-result-stable-retry-and-duplicate-ack-no-extra-delivery")

        # A different runtime represents a game load. It must not recover old
        # earnings or let old external records prevent reassessment.
        identity = secrets.token_hex(16)
        observed(manager, identity)
        assert not manager.pending_rewards(identity, {})
        again = manager.select(identity, 12, 1)
        manager.prepare(identity)
        environment = manager.environments[identity]
        container = environment.container_id
        command(environment, ["python3", "-c", "open('/home/nixboy/clean-witness','w').write('current runtime')"])
        observed(manager, identity)
        assert manager.snapshot(identity)["attempt"]["id"] == again["id"]
        assert manager.environments[identity].container_id == container
        command(environment, ["test", "-f", "/home/nixboy/clean-witness"])
        checks.append("normal-runtime-reopen-preserves-attempt-container-and-files")
        solve(manager, identity)
        assert manager.check(identity)["rewarded"]
        fresh = manager.pending_rewards(identity, {})
        assert len(fresh) == 1 and fresh[0][-1] != receipt
        # Lose the ACK entirely, then load another runtime: no historical replay.
        identity = secrets.token_hex(16)
        observed(manager, identity)
        assert not manager.pending_rewards(identity, {})
        checks.append("rolled-back-character-can-reearn-and-new-runtime-discards-lost-ack-pending")
        return {"passed": True, "checks": checks, "first_receipt": receipt,
                "reearned_receipt": fresh[0][-1]}
    finally:
        manager.close()


def assessments(output):
    state = output/"assessment-state"
    manager = AttemptManager(state, local_docker_client, Catalog(HERE/"content-assessment-test"))
    checks = []
    identity = secrets.token_hex(16)
    try:
        observed(manager, identity)
        manager.select_course(identity, "test.intro")
        attempt = manager.snapshot(identity)["attempt"]
        alternative = manager.catalog.course.alternative(attempt["course_gate"], attempt["alternative"])
        manager.draft_answer(identity, alternative.questions[0].id, alternative.questions[0].answers[0])
        rejection(lambda: manager.commit_answers(identity))
        before = manager.questions(identity)
        assert not before["committed"]
        observed(manager, identity)
        assert manager.questions(identity)["answers"] == before["answers"]
        answer(manager, identity)
        assert manager.check(identity)["status"] == "passed"
        checks.append("bounded-theory-draft-reopen-incomplete-commit-and-final-grading")

        manager.select_course(identity, "test.mixed")
        manager.prepare(identity)
        solve(manager, identity)
        assert manager.check(identity)["status"] != "passed"
        answer(manager, identity)
        assert manager.check(identity)["status"] == "passed"
        final = manager.snapshot(identity)
        assert not set(final) & {"education", "receipts", "perk_receipts", "passes"}
        checks.append("mixed-needs-both-practical-and-committed-theory-without-achievement-ledger")
        return {"passed": True, "checks": checks}
    finally:
        manager.close()


def guest_command(environment, text):
    result = environment.guest(text, timeout=30)
    assert result.get("exitcode") == 0, {"command": text, "result": result}
    return result.get("stdout", "")


def native(output, offline_state=None, runtime=None, keep_pending=False):
    from nginx_profile import configuration
    state = offline_state or output/"native-state"
    manager = AttemptManager(state, local_docker_client, Catalog(HERE/"content-delivery-test"))
    identity = runtime or secrets.token_hex(16)
    checks = []
    try:
        if runtime is not None:
            manager.snapshot(runtime)  # Never clear game state for a typo/new ID.
        observed(manager, identity)
        rejection(lambda: manager.select_course(identity, "delivery.nginx.2"))
        previous_id, previous_boot = None, None
        for stage, preparation in ((1, "nginx-policy-v1"), (2, "nginx-reload-v1")):
            attempt = manager.select_course(identity, f"delivery.nginx.{stage}")
            manager.prepare(identity, progress=print)
            environment = manager.environments[identity]
            boot = environment._probe()["boot_id"]
            if stage == 1:
                guest_command(environment, "printf stage-one > /root/clean-stage-witness")
            else:
                assert attempt["id"] != previous_id and boot != previous_boot
                guest_command(environment, "test ! -e /root/clean-stage-witness")
            target = configuration(preparation, solved=True)
            guest_command(environment, "printf %s "+shlex.quote(target)+" > /etc/nginx/nginx.conf && nginx -t && systemctl reload nginx")
            time.sleep(.3)
            assert manager.checkpoint(identity, "reload")["status"] == "passed"
            if stage == 2:
                answer(manager, identity)
            result = manager.check(identity)
            assert result["status"] == "passed", result
            assert identity not in manager.environments
            previous_id, previous_boot = attempt["id"], boot
        pending = manager.pending_perks(identity, {})
        assert len(pending) == 1
        profile, perk, rank, receipt = pending[0]
        assert (profile, perk, rank) == (identity, 12, 2)
        assert manager.pending_perks(identity, {}) == pending
        checks.append("native-project-stages-use-fresh-canonical-guests-and-trusted-reload-checkpoints")
        if keep_pending:
            return {"passed": True, "checks": checks, "receipt": receipt, "runtime": identity}
        manager.acknowledge_perk(identity, profile, perk, rank, receipt, True)
        manager.acknowledge_perk(identity, profile, perk, rank, receipt, True)
        assert not manager.pending_perks(identity, {})
        checks.append("one-eligible-passive-perk-result-with-stable-duplicate-ack")
        identity = secrets.token_hex(16)
        observed(manager, identity)
        assert not manager.pending_perks(identity, {})
        rejection(lambda: manager.select_course(identity, "delivery.nginx.2"))
        assert not manager.snapshot(identity).get("project")
        checks.append("new-runtime-removes-project-cursor-and-never-replays-completed-perk")
        return {"passed": True, "checks": checks, "receipt": receipt}
    finally:
        manager.close()


def earn_skill(output, offline_state, runtime, catalog_name):
    content = "content" if catalog_name == "default" else "content-delivery-test"
    manager = AttemptManager(offline_state, local_docker_client, Catalog(HERE/content))
    try:
        # A known current runtime must already have been opened by the game.
        manager.snapshot(runtime)
        observed(manager, runtime)
        level = manager.current_level(runtime, 12)
        attempt = manager.snapshot(runtime).get("attempt")
        if attempt is None:
            manager.select(runtime, 12, level)
        else:
            assert attempt.get("skill") == 12 and attempt.get("level") == level
        manager.prepare(runtime)
        solve(manager, runtime)
        result = manager.check(runtime)
        assert result["status"] == "passed" and result["rewarded"], result
        pending = manager.pending_rewards(runtime, {})
        assert len(pending) == 1
        return {"passed": True, "runtime": runtime, "receipt": pending[0][-1],
                "skill": pending[0][0], "level": pending[0][1], "points": pending[0][2],
                "scope": "genuine pending result from real validator; no ACK/game effect simulated"}
    finally:
        manager.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--native", action="store_true", help="also run two real prepared native nginx guests")
    parser.add_argument("--earn-only", action="store_true", help="earn a pending result for an explicitly stopped disposable game provider")
    parser.add_argument("--offline-state", type=Path)
    parser.add_argument("--runtime")
    parser.add_argument("--catalog", choices=("default", "delivery"), default="default")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.earn_only and not (args.offline_state and args.runtime)) or (
            not args.earn_only and (args.offline_state or args.runtime)):
        parser.error("--earn-only requires both --offline-state and --runtime; never use live state")
    if args.runtime is not None and (len(args.runtime) != 32 or args.runtime == "0"*32 or
            any(character not in "0123456789abcdef" for character in args.runtime)):
        parser.error("--runtime must be the exact nonzero lowercase runtime ID from the disposable game")
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = {"passed": False, "scope": "real Docker/manager validation and delivery contract; no actual game effect/save claim"}
    try:
        if args.earn_only:
            summary["earned"] = (native(args.output, args.offline_state, args.runtime, True) if args.native else
                                 earn_skill(args.output, args.offline_state, args.runtime, args.catalog))
        else:
            summary["ordinary"] = ordinary(args.output)
            summary["assessments"] = assessments(args.output)
            if args.native:
                summary["native"] = native(args.output)
        summary["passed"] = True
    finally:
        states = ({"offline-game-state": args.offline_state} if args.earn_only else
                  {name: args.output/name for name in ("ordinary-state", "assessment-state", "native-state")
                   if (args.output/name).exists()})
        # The manager closes registered environments. A developer SIGINT may
        # unwind partial preparation before registration; reuse owner-scoped
        # orphan cleanup for these explicitly disposable, stopped-provider states.
        if not summary["passed"]:
            for state in states.values():
                client = local_docker_client()
                try:
                    cleanup_environments(client, state)
                finally:
                    client.close()
        summary["cleanup"] = {name: resources(state) for name, state in states.items()}
        summary["elapsed_seconds"] = round(time.monotonic()-started, 3)
        (args.output/"summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
        assert not any(any(value.values()) for value in summary["cleanup"].values()), summary["cleanup"]
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
