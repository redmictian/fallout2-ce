"""Opt-in real Docker/QEMU native-service/profile acceptance; no game or saves."""
import argparse
import json
from pathlib import Path
import secrets
import shlex
import time
from types import SimpleNamespace
from unittest import mock

from bridge import local_docker_client
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace
from nginx_env import NginxEnvironment, manifest
from nginx_profile import configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run:
        parser.error("explicit --run is required")
    args.output.mkdir(parents=True, exist_ok=False)
    environments, states, checks = [], [], []
    started = time.monotonic()
    summary = {"passed": False, "scope": "real native kernel/systemd/nginx and independent netns clients; no game/save/reward claim"}

    def save(name, value):
        (args.output / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")

    def environment(preparation, suffix):
        state = args.output / suffix
        states.append(state)
        spec = SimpleNamespace(kind="qemu-nginx", parameters={"name": ["native-smoke"], "preparation": [preparation]},
                               title="Native service fixture", instructions="Repair the policy/configuration, reload the native service, then submit.")
        result = NginxEnvironment(local_docker_client(), spec, secrets.token_hex(16), 1, 0, state)
        environments.append(result)
        return result

    def command(env, text):
        result = env.guest(text, timeout=25)
        if result.get("exitcode") != 0 or result.get("truncated"):
            raise AssertionError(result)
        return result

    def configure(env, text, operation="reload"):
        command(env, "printf %s " + shlex.quote(text) + " > /etc/nginx/nginx.conf; nginx -t && systemctl " + operation + " nginx")
        time.sleep(.5)

    try:
        first = environment("nginx-policy-v1", "stage-one")
        stage_start = time.monotonic()
        first.start(progress=lambda text: print(text, flush=True))
        summary["stage_one_ready_seconds"] = time.monotonic() - stage_start
        result = first.check()
        save("initial-open-policy", result)
        assert result["status"] == "incomplete" and not result["evidence"]["checks"]["denied_staff"]
        checks.append("initial-open-policy-is-unsolved-not-facility-failure")
        configure(first, configuration(first.preparation_id, solved=True))
        result = first.check()
        save("valid-policy", result)
        assert result["status"] == "passed"
        checkpoint = first.checkpoint("reload")
        save("valid-native-reload", checkpoint)
        assert checkpoint["status"] == "passed"
        save("distinct-client-real-probe", first._last_probe)
        checks.append("native-service-manager-journal-distinct-sources-full-bodies-unprivileged-workers-and-reload")

        command(first, "ip -n nb-client-denied link set eth0 down")
        result = first.check()
        save("broken-client", result)
        assert result["status"] == "error" and result["facility_failure"]
        command(first, "ip -n nb-client-denied link set eth0 up")
        checks.append("broken-negative-client-does-not-pass-as-policy-denial")

        configure(first, configuration(first.preparation_id))
        result = first.check()
        save("incorrect-policy", result)
        assert result["status"] == "incomplete"
        checks.append("permissive-policy-does-not-pass")

        configure(first, configuration(first.preparation_id, solved=True).replace("user www-data;", "user root;"))
        result = first.check()
        save("root-worker-workaround", result)
        assert result["status"] == "incomplete" and not result["evidence"]["checks"]["nonprivileged_workers"]
        checks.append("root-worker-permission-workaround-does-not-pass")

        configure(first, configuration(first.preparation_id, solved=True).replace("server_name lesson.nixboy.invalid;", "server_name wrong.invalid;"))
        result = first.check()
        save("wrong-host-response", result)
        assert result["status"] == "error" and result["facility_failure"]
        checks.append("wrong-host-response-is-not-policy-evidence")

        configure(first, configuration(first.preparation_id, solved=True))
        command(first, "systemctl stop nginx")
        result = first.check()
        save("stopped-server", result)
        assert result["status"] == "error" and result["facility_failure"]
        command(first, "systemctl start nginx")
        result = first.checkpoint("reload")
        save("restart-is-not-reload", result)
        assert result["status"] == "incomplete" and not result["evidence"]["checks"]["same_master"]
        checks.append("stopped-server-not-denial-and-restart-not-reload")
        command(first, "printf 'learner-personal-state\n' > /root/stage-one-witness")
        first.close()

        second = environment("nginx-reload-v1", "stage-two")
        stage_start = time.monotonic()
        second.start(progress=lambda text: print(text, flush=True))
        summary["stage_two_ready_seconds"] = time.monotonic() - stage_start
        command(second, "test ! -e /root/stage-one-witness")
        result = second.check()
        save("canonical-stage-two", result)
        assert result["status"] == "incomplete" and result["evidence"]["checks"]["denied_staff"] \
            and not result["evidence"]["checks"]["stage_response"]
        configure(second, configuration(second.preparation_id, solved=True))
        result = second.checkpoint("reload")
        save("stage-two-reload", result)
        assert result["status"] == "passed"
        assert second.check()["status"] == "passed"
        second.close()
        checks.append("fresh-stage-two-has-stage-one-contract-current-fault-and-no-personal-state")

        invalid = environment("nginx-policy-v1", "invalid-preparation")
        try:
            with mock.patch("nginx_env.configuration", side_effect=lambda name: configuration(name, solved=True)):
                invalid.start()
        except RuntimeError as exc:
            save("invalid-preparation", {"rejected": True, "error": str(exc)})
        else:
            raise AssertionError("incorrect prepared state was accepted")
        assert invalid.container_id is None
        checks.append("healthy-but-incorrect-canonical-preparation-is-rejected-and-cleaned")
        summary.update(passed=True, checks=checks, manifest=manifest())
    except Exception as exc:
        summary["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        for env in environments:
            env.close()
        client = local_docker_client()
        try:
            cleanup = {}
            for state in states:
                labels = [TRAINING_LABEL + "=1", OWNER_LABEL + "=" + owner_namespace(state)]
                cleanup[state.name] = {"containers": [item["Id"] for item in client.api.containers(all=True, filters={"label": labels})],
                                       "volumes": [item["Name"] for item in client.api.volumes(filters={"label": labels}).get("Volumes") or []]}
            summary["cleanup"] = cleanup
            assert not any(any(resources.values()) for resources in cleanup.values())
        finally:
            client.close()
        summary["elapsed_seconds"] = time.monotonic() - started
        save("summary", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
