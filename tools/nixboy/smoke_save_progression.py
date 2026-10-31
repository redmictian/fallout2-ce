"""Opt-in real Docker save/restore invalidation; no game or simulated rewards."""
import argparse
import json
from pathlib import Path
import secrets
import time

from bridge import local_docker_client
from challenge_content import Catalog
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace
from progression_state import ZERO_SNAPSHOT
from protocol import SKILL_NAMES
from training import AttemptManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    state = args.output/"state"
    identity, epoch = secrets.token_hex(16), secrets.token_hex(16)
    context = {"training.version": 6, **{"skill."+name: 10 for name in SKILL_NAMES}}
    context.update({f"training.profile.{i}": int.from_bytes(bytes.fromhex(identity)[i*4:i*4+4], "big", signed=True) for i in range(4)})
    manager = AttemptManager(state, local_docker_client, Catalog(Path(__file__).parent/"content-assessment-test"))
    started = time.monotonic()
    checks = []
    summary = {"passed": False, "scope": "Real direct-container save-selected attempt invalidation/reissue; no game", "checks": checks}
    try:
        manager.activate_progression(identity, epoch, ZERO_SNAPSHOT, True, {}, {}, context)
        selected = manager.select(identity, 12, 61000)
        old_container = manager.prepare(identity)
        env = manager.environments[identity]
        assert env._exec("workstation", ["python3", "-c", "open('/home/nixboy/load-witness','w').write('later runtime')"])[0] == 0
        saved = manager.capture_progression(identity, epoch, {}, {}, context)
        before = manager.snapshot(identity)
        manager.activate_progression(identity, secrets.token_hex(16), saved, True, {}, {}, context)
        after = manager.snapshot(identity)
        assert not manager.environments
        assert after["attempt"]["id"] != before["attempt"]["id"]
        assert after["attempt"]["generation"] == before["attempt"]["generation"]+1
        assert after["attempt"]["challenge"] == selected["challenge"]
        assert after["queues"] == before["queues"] and not after["receipts"]
        checks.append("load-invalidates-live-container-and-evidence-without-consuming-alternative")
        client = local_docker_client()
        try:
            try:
                client.api.inspect_container(old_container)
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 404
            else:
                raise AssertionError("old container still exists")
        finally:
            client.close()
        current = manager.prepare(identity)
        assert current != old_container
        checked = manager.environments[identity]._exec("workstation", ["test", "!", "-e", "/home/nixboy/load-witness"])
        assert checked[0] == 0, checked
        checks.append("reissue-is-fresh-canonical-container-with-no-restored-process-or-file")
        summary["passed"] = True
    finally:
        manager.close()
        client = local_docker_client()
        labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state)}"]
        try:
            summary["cleanup"] = {
                "containers": [item["Id"] for item in client.api.containers(all=True, filters={"label": labels})],
                "networks": [item["Id"] for item in client.api.networks(filters={"label": labels})],
                "volumes": [item["Name"] for item in client.api.volumes(filters={"label": labels}).get("Volumes") or []]}
        finally:
            client.close()
        summary["elapsed_seconds"] = round(time.monotonic()-started, 3)
        (args.output/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")
        assert not any(summary["cleanup"].values()), summary["cleanup"]
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
