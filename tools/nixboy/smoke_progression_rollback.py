"""Earn the existing project headlessly for a stopped disposable game provider.

No reward or completion is manufactured. Real prepared guests, reload checkpoints
and final trusted practical/conceptual validation run through AttemptManager.
The current runtime activation is required; this helper never chooses a save.
"""
import argparse
import json
from pathlib import Path
import time

from bridge import local_docker_client
from challenge_content import Catalog
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace
from smoke_delivery import context, repair
from training import AttemptManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--offline-state", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--activation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manager = AttemptManager(args.offline_state, local_docker_client,
                             Catalog(Path(__file__).parent/"content-delivery-test"))
    started = time.monotonic()
    result = {"passed": False, "profile": args.profile, "activation": args.activation,
              "scope": "Real native project validation; no game effect/save claim", "stages": []}
    try:
        manager.assert_activation(args.profile, args.activation)
        values = {**context(args.profile), "training.version": 6}
        manager.observe(args.profile, {}, values)
        for stage in (1, 2):
            gate = f"delivery.nginx.{stage}"
            manager.select_course(args.profile, gate)
            manager.prepare(args.profile, progress=print)
            repair(manager.environments[args.profile], stage)
            assert manager.checkpoint(args.profile, "reload")["status"] == "passed"
            if stage == 2:
                manager.draft_answer(args.profile, "q1", "1")
                assert manager.commit_answers(args.profile)["passed"]
            checked = manager.check(args.profile)
            assert checked["status"] == "passed", checked
            result["stages"].append(gate)
        receipt = manager.snapshot(args.profile)["perk_receipts"]["12:2"]["receipt"]
        result.update(passed=True, receipt=receipt)
    finally:
        manager.close()
        client = local_docker_client()
        labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(args.offline_state)}"]
        try:
            result["cleanup"] = {
                "containers": [item["Id"] for item in client.api.containers(all=True, filters={"label": labels})],
                "networks": [item["Id"] for item in client.api.networks(filters={"label": labels})],
                "volumes": [item["Name"] for item in client.api.volumes(filters={"label": labels}).get("Volumes") or []]}
        finally:
            client.close()
        result["elapsed_seconds"] = round(time.monotonic()-started, 3)
        (args.output/"summary.json").write_text(json.dumps(result, indent=2)+"\n")
        assert not any(result["cleanup"].values()), result["cleanup"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
