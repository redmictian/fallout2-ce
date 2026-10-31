"""Opt-in real Docker/manager/bridge project acceptance; no game-effect simulation.

An optional stopped-provider state/profile can earn a genuine pending receipt
for subsequent actual-game delivery. Never run against a live provider's state.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import socket
import struct
import threading
import time

from bridge import BridgeServer, local_docker_client
from challenge_content import Catalog
from challenge_env import owner_namespace, OWNER_LABEL, TRAINING_LABEL
from protocol import (AUTH, TRAIN_OPEN, TRAIN_PERK_REWARD, TRAIN_PERK_ACK, READY,
                      OUTPUT, ERROR, CLOSE, SKILL_NAMES, frame, read_frame,
                      encode_training_open, decode_training_perk_reward)
from training import AttemptManager

HERE = Path(__file__).resolve().parent


def context(identity):
    values = {"training.version": 5, "perk.training.enabled": 1, "perk.toughness": 1,
              "perk.toughness.max": 4, "stat.damage_resistance": 0}
    values.update({"skill." + name: 10 for name in SKILL_NAMES})
    for i in range(4):
        values[f"training.profile.{i}"] = int.from_bytes(bytes.fromhex(identity)[i*4:i*4+4], "big", signed=True)
    return values


def require_guest(environment, command):
    result = environment.guest(command, timeout=30)
    if result.get("exitcode") != 0:
        raise AssertionError({"operation": command, "result": result})
    return result.get("stdout", "")


def repair(environment, stage):
    from nginx_profile import configuration
    # The exact validated target is a test solution; learners may use any
    # equivalent real nginx configuration preserving the declared invariants.
    name = "nginx-policy-v1" if stage == 1 else "nginx-reload-v1"
    target = configuration(name, solved=True)
    require_guest(environment, "printf %s " + shlex.quote(target) + " > /etc/nginx/nginx.conf && nginx -t && systemctl reload nginx")
    time.sleep(.3)


def snapshot(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def rejected(operation):
    try:
        operation()
    except (ValueError, RuntimeError) as exc:
        return str(exc)
    raise AssertionError("invalid operation was accepted")


def delivery(server, token, identity, claimed=None, acknowledge=False):
    with socket.create_connection(server.server_address, timeout=15) as channel:
        channel.sendall(frame(AUTH, token) + frame(TRAIN_OPEN, encode_training_open(40, 153, context(identity), {}, claimed or {})))
        for _ in range(20):
            kind, payload = read_frame(channel)
            if kind == ERROR:
                raise AssertionError(payload.decode())
            if kind == TRAIN_PERK_REWARD:
                result = decode_training_perk_reward(payload)
                if acknowledge:
                    channel.sendall(frame(TRAIN_PERK_ACK, payload + b"\1"))
                    time.sleep(.15)
                channel.sendall(frame(CLOSE, b""))
                return result
        raise AssertionError("expected genuine earned perk delivery")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline-state", type=Path)
    parser.add_argument("--profile")
    args = parser.parse_args()
    if not args.run:
        parser.error("explicit --run is required")
    if args.offline_state and not args.profile:
        parser.error("a stopped-provider state requires its explicit disposable profile")
    args.output.mkdir(parents=True, exist_ok=False)
    state = args.offline_state or args.output / "state"
    identity = args.profile or secrets.token_hex(16)
    if len(identity) != 32 or any(ch not in "0123456789abcdef" for ch in identity):
        parser.error("profile must be 16 bytes of lowercase hex")
    manager = AttemptManager(state, local_docker_client, Catalog(HERE / "content-delivery-test"))
    manager.observe(identity, {}, context(identity))
    manager.observe_perks(identity, {})
    started = time.monotonic()
    checks = []
    summary = {"passed": False, "profile": identity, "checks": checks, "scope": "real Docker/manager/bridge; no actual game effect/save claim"}
    server = None
    try:
        rejected(lambda: manager.select_course(identity, "delivery.nginx.2"))
        checks.append("skipped-stage-rejected")
        first = manager.select_course(identity, "delivery.nginx.1")
        manager.prepare(identity, progress=print)
        environment = manager.environments[identity]
        require_guest(environment, "printf stage-one-only > /root/stage-one-witness")
        first_boot = environment._probe()["boot_id"]
        assert manager.check(identity)["status"] == "pending"
        repair(environment, 1)
        assert manager.checkpoint(identity, "reload")["status"] == "passed"
        assert manager.check(identity)["status"] == "passed"
        assert identity not in manager.environments
        snapshot(args.output / "stage-one.json", manager.snapshot(identity))
        checks.append("native-stage-one-reload-completes-and-environment-is-removed")
        second = manager.select_course(identity, "delivery.nginx.2")
        assert second["id"] != first["id"]
        manager.prepare(identity, progress=print)
        environment = manager.environments[identity]
        require_guest(environment, "test ! -e /root/stage-one-witness")
        assert environment._probe()["boot_id"] != first_boot
        binding = {"attempt": first["id"], "generation": first["generation"], "content": first["course_digest"]}
        rejected(lambda: manager.checkpoint(identity, "reload", expected=binding))
        checks.append("stage-two-is-independent-canonical-state-no-carried-files-or-stale-checkpoint")
        require_guest(environment, "printf reset-witness > /root/stage-two-witness")
        old_generation = second["generation"]
        manager.reset(identity, progress=print)
        environment = manager.environments[identity]
        require_guest(environment, "test ! -e /root/stage-two-witness")
        current = manager.snapshot(identity)
        assert current["attempt"]["generation"] == old_generation + 1
        assert "delivery.nginx.1" in current["education"]["completions"]
        assert environment._decide(environment._probe(), initial=True)["stage_response"]
        checks.append("stage-two-reset-restores-own-canonical-fault-preserves-stage-one-completion")
        repair(environment, 2)
        assert manager.checkpoint(identity, "reload")["status"] == "passed"
        manager.draft_answer(identity, "q1", "1")
        manager.commit_answers(identity)
        result = manager.check(identity)
        assert result["status"] == "passed" and result["perk_rewarded"]
        snapshot(args.output / "completed-profile.json", manager.snapshot(identity))
        checks.append("genuine-mixed-stage-two-earns-durable-test-only-passive-perk-receipt")
        token = secrets.token_hex(32).encode()
        server = BridgeServer(0, token, "fallout2-nixboy:dev", client_factory=local_docker_client, training_manager=manager)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        first_delivery = delivery(server, token, identity)
        assert server.handlers_done.wait(5)
        repeat_delivery = delivery(server, token, identity)
        assert first_delivery == repeat_delivery
        assert first_delivery[-1] == manager.pending_perks(identity, {})[0][-1]
        checks.append("lost-ack-reopen-replays-same-profile-bound-earned-receipt")
        summary.update(passed=True, receipt=first_delivery[-1])
    finally:
        if server:
            server.shutdown()
            server.server_close()
        else:
            manager.close()
        client = local_docker_client()
        labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state)}"]
        try:
            cleanup = {"containers": [item["Id"] for item in client.api.containers(all=True, filters={"label": labels})],
                       "networks": [item["Id"] for item in client.api.networks(filters={"label": labels})],
                       "volumes": [item["Name"] for item in client.api.volumes(filters={"label": labels}).get("Volumes", [])]}
        finally:
            client.close()
        summary.update(elapsed_seconds=round(time.monotonic()-started, 3), cleanup=cleanup)
        snapshot(args.output / "summary.json", summary)
        assert not any(cleanup.values()), cleanup
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
