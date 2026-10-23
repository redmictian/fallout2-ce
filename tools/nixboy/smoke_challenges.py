#!/usr/bin/env python3
"""Opt-in all-content real Compose fixture/reference-solution acceptance test."""

import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import time

from challenge_content import Catalog
from challenge_env import ComposeEnvironment, cleanup_owned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="create and remove owned disposable exercise resources")
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--challenge", action="append", help="restrict to these content identifiers")
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to create disposable Docker exercise environments")
    import docker
    endpoint = "unix:///var/run/docker.sock"
    catalog = Catalog()
    loader = importlib.util.spec_from_file_location("training_reference", Path(__file__).parent / "tests" / "training_reference.py")
    reference = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(reference)
    checks = []
    with tempfile.TemporaryDirectory(prefix="nixboy-acceptance-") as state:
        for identifier in args.challenge or list(catalog.challenges):
            spec = catalog.get(identifier)
            started = time.monotonic()
            env = ComposeEnvironment(docker.DockerClient(base_url=endpoint, timeout=10), spec,
                                     "smoke-" + secrets.token_hex(8), args.seed, 0, state,
                                     params=catalog.instantiate(identifier, args.seed))
            try:
                env.start()  # Includes readiness, setup and unsolved-fixture preflight.
                reference.wrong(env)
                initial = env.check()
                if initial["status"] != "incomplete":
                    raise AssertionError((identifier, "wrong solution", initial))
                # Reset removes deliberate wrong-state damage and proves deterministic setup.
                original_params = dict(env.params)
                env.reset()
                if env.params != original_params:
                    raise AssertionError("reset changed exercise parameters")
                reference.solve(env)
                result = env.check()
                if result["status"] != "passed" or env.check()["status"] != "passed":
                    raise AssertionError((identifier, "reference solution", result))
                if spec.kind in ("filter-lines", "sort-unique", "extract-field"):
                    env._write("workstation", "/home/nixboy/lab/result.txt", env.expected.rstrip("\n"))
                    if env.check()["status"] != "passed":
                        raise AssertionError("equivalent record output without final newline rejected")
                if env.network:
                    env.client.api.stop(env.services["server"], timeout=1)
                    if env.check()["status"] != "error":
                        raise AssertionError("stopped dependency must be an environment error")
                checks.append({"challenge": identifier, "seed": args.seed, "status": "passed",
                               "seconds": round(time.monotonic() - started, 2)})
                print(json.dumps(checks[-1]), flush=True)
            finally:
                env.close()
        client = docker.DockerClient(base_url=endpoint, timeout=10)
        try:
            cleanup_owned(client, state)
            labels = ["org.fallout2-ce.nixboy.training=1"]
            remaining = [item for item in client.api.containers(all=True, filters={"label": labels})
                         if item.get("Labels", {}).get("org.fallout2-ce.nixboy.owner") == env.owner]
            if remaining:
                raise AssertionError("acceptance leaked owned containers")
        finally:
            client.close()
    print(json.dumps({"status": "passed", "count": len(checks), "checks": checks}), flush=True)


if __name__ == "__main__":
    main()
