#!/usr/bin/env python3
"""Opt-in actual bridge socket -> QEMU serial -> trusted receipt smoke.

This drives the existing production protocol and real disposable environments.
Its client models the game ledger; this is not evidence of actual game UI or
SAVE.DAT behavior. Use the separate in-game acceptance and read-only save inspector
for those boundaries. No production state directory or player profile is used.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import shlex
import threading
import time

from bridge import BridgeServer, local_docker_client
from challenge_content import Catalog
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace
from protocol import TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET, TRAIN_ABANDON
from smoke_training import Probe, ShellSolution
from training import AttemptManager

ROOT = Path(__file__).resolve().parent


class QemuProbe(Probe):
    def start_guest(self, practice=False):
        number = list(self.manager.catalog.tracks).index(12) + 1
        self.input(str(number).encode() + b"\r" + (b"p\r" if practice else b"a\r"))
        self.until_output(b"root@nbq-debian", seconds=360)
        return self.manager.snapshot(self.identity)["attempt"]

    def resume_guest(self):
        self.input(b"r\r")
        self.until_output(b"root@nbq-debian", seconds=360)

    def script(self, text):
        code, output, error = self.command(["/bin/bash", "--noprofile", "--norc", "-c", text])
        if code:
            raise AssertionError({"command": text, "code": code, "output": output, "error": error})
        return output


def save(directory, name, value):
    (directory / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")


def guest_ready(environment, previous=None, timeout=240):
    """Only read guest state; learner mutations remain on the serial INPUT path."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = environment.guest("cat /proc/sys/kernel/random/boot_id; systemctl is-active multi-user.target", timeout=5)
            lines = value.get("stdout", "").splitlines()
            if value.get("exitcode") == 0 and len(lines) == 2 and lines[1] == "active" \
                    and (previous is None or lines[0] != previous):
                return {"boot_id": lines[0], "observed_unix": time.time()}
            last = value
        except Exception as exc:
            last = str(exc)
        time.sleep(.5)
    raise TimeoutError("normal guest boot: " + str(last))


def current_boot(probe):
    marker = "NBQ_BOOT_ID"
    output = probe.script("printf '" + marker + "='; cat /proc/sys/kernel/random/boot_id")
    import re
    match = re.search(marker + r"=([0-9a-f-]{36})", output)
    if not match:
        raise AssertionError(output)
    return match.group(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="new private disposable evidence/state directory")
    parser.add_argument("--content-dir", type=Path, default=ROOT / "content-qemu-pilot")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required to create actual disposable training environments")
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("output directory must be new or empty; never point this at player training state")
    out.mkdir(parents=True, exist_ok=True)
    state = out / "state"
    manifest = json.loads((ROOT / "container/qemu/bt05-manifest.json").read_text())
    expected = manifest["expected"]
    factory = local_docker_client
    manager = AttemptManager(state, factory, catalog=Catalog(args.content_dir))
    token = secrets.token_hex(32).encode()
    server = BridgeServer(0, token, "fallout2-nixboy:dev", client_factory=factory, training_manager=manager)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    probe = QemuProbe(server, token, manager)
    checks = []
    summary = {"passed": False, "scope": "real socket/Docker/QEMU; modeled game ledger, no game UI/save claim", "checks": checks}

    def environment():
        return manager.environments[probe.identity]

    def nonpass(label):
        probe.action(TRAIN_CHECK)
        probe.until_output(b"Incomplete.", seconds=180)
        snap = manager.snapshot(probe.identity)
        assert not manager.pending_rewards(probe.identity, probe.progress())
        assert snap.get("attempt") is None, snap
        save(out, label, {"profile": snap, "terminal": probe.output.decode("utf-8", "replace")})
        checks.append(label)

    def reboot():
        old = current_boot(probe)
        probe.input(b"systemctl reboot\r")
        return guest_ready(environment(), old)

    def fstab(uuid):
        mount = expected["mount"]
        line = "" if uuid is None else "UUID=" + uuid + " " + mount + " ext4 defaults,x-systemd.device-timeout=60s 0 2"
        code = "from pathlib import Path; p=Path('/etc/fstab'); lines=[s for s in p.read_text().splitlines() if '/srv/recovery-data' not in s]; p.write_text('\\n'.join(lines+" + repr([line] if line else []) + ")+ '\\n')"
        probe.script("python3 -c " + shlex.quote(code) + "; systemctl daemon-reload")

    try:
        probe.connect()
        initial = probe.start_guest()
        # Expected emergency/agent absence is not a learner-failure submission.
        before = manager.snapshot(probe.identity)
        probe.action(TRAIN_CHECK)
        probe.until_output(b"Environment/validator error", seconds=90)
        after = manager.snapshot(probe.identity)
        assert after["attempt"]["id"] == before["attempt"]["id"]
        assert after["queues"] == before["queues"] and not after["receipts"]
        checks.append("expected-recovery-is-not-learner-failure")
        probe.resume_guest()

        # Actual serial shell changes survive hub detach and socket reopen.
        original_boot = current_boot(probe)
        probe.script("printf 'kept\\n' > /root/bt05-smoke-witness")
        container = environment().container_id
        probe.action(TRAIN_HUB)
        probe.until_output(b"Choose a number")
        probe.close()
        probe.connect()
        probe.resume_guest()
        assert environment().container_id == container and current_boot(probe) == original_boot
        probe.script("test -f /root/bt05-smoke-witness")
        checks.append("serial-detach-reopen-same-guest-and-disk")

        probe.action(TRAIN_RESET)
        probe.until_output(b"root@nbq-debian", seconds=360)
        reset = manager.snapshot(probe.identity)["attempt"]
        assert reset["id"] == initial["id"] and reset["generation"] == initial["generation"] + 1
        probe.script("test ! -e /root/bt05-smoke-witness; grep -q 00000000-0000-0000-0000-00000000ba05 /etc/fstab")
        checks.append("reset-discards-overlay-restores-canonical-fault")
        fstab(None)
        save(out, "missing-mount-boot", reboot())
        nonpass("missing-required-mount-rejected")

        probe.start_guest()
        fstab(expected["decoy_uuid"])
        save(out, "decoy-boot", reboot())
        # The external validator must reject even identical payload/HTTP success.
        nonpass("identical-payload-decoy-filesystem-rejected")

        # A lost outer runner is a facility fault, with neither reward nor rotation.
        probe.start_guest()
        snap = manager.snapshot(probe.identity)
        inspector = factory()
        try:
            inspector.api.stop(environment().container_id, timeout=5)
        finally:
            inspector.close()
        probe.action(TRAIN_CHECK)
        probe.until_output(b"Environment/validator error", seconds=90)
        fault = manager.snapshot(probe.identity)
        assert fault["attempt"]["id"] == snap["attempt"]["id"]
        assert fault["queues"] == snap["queues"] and not fault["receipts"]
        save(out, "facility-failure", {"profile": fault, "terminal": probe.output.decode("utf-8", "replace")})
        checks.append("runner-loss-facility-error-no-reward-no-rotation")
        probe.action(TRAIN_RESET)
        probe.until_output(b"root@nbq-debian", seconds=360)

        fstab(expected["data_uuid"])
        save(out, "valid-repaired-boot", reboot())
        probe.action(TRAIN_CHECK)
        probe.until_output(b"Skill reward earned", seconds=180)
        reward = probe.reward()
        snap = manager.snapshot(probe.identity)
        assert set(snap["receipts"]) == {"12:60000"}
        assert snap["receipts"]["12:60000"]["points"] == 1
        save(out, "trusted-completion", snap)
        checks.append("serial-repair-real-reboot-trusted-validation-one-receipt")

        probe.close(explicit=False) # Lose ACK after actual validation, as existing contract permits.
        probe.connect()
        assert probe.reward() == reward
        probe.accept(reward)
        probe.until_output(b"completed 1/")
        once = dict(probe.receipts)
        awarded = probe.skill_values[12]
        probe.close()
        probe.connect()
        assert probe.receipts == once and probe.skill_values[12] == awarded
        assert not manager.pending_rewards(probe.identity, probe.progress())
        checks.append("lost-ack-exact-replay-and-reopen-no-extra-bonus")

        # Model loading an older save of the SAME profile, not another character.
        probe.close()
        probe.receipts.clear()
        probe.skill_values[12] = awarded - 1
        probe.connect()
        assert probe.reward() == reward
        probe.accept(reward)
        probe.until_output(b"completed 1/")
        assert probe.receipts == once and probe.skill_values[12] == awarded
        checks.append("older-same-profile-ledger-receives-same-receipt-once")

        loader = importlib.util.spec_from_file_location("training_reference", ROOT / "tests/training_reference.py")
        reference = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(reference)
        ordinary_seen = set()
        for practice in (False, True):
            attempt = probe.start(3, practice=practice)
            assert attempt["level"] == 60001
            ordinary_seen.add(attempt["challenge"])
            reference.solve(ShellSolution(probe, attempt, manager.catalog))
            probe.action(TRAIN_CHECK)
            probe.until_output(b"Practice completed; no reward" if practice else b"Skill reward earned")
            if not practice:
                ordinary_reward = probe.reward()
                probe.accept(ordinary_reward)
                probe.until_output(b"completed 1/")
        assert len(ordinary_seen) == 2, ordinary_seen
        checks.append({"ordinary-direct-container-alternatives": sorted(ordinary_seen), "assessment_reward": 1,
                       "practice_reward": 0})
        summary.update(passed=True, profile=probe.identity, claims=probe.progress(), effective_model_skills=probe.skill_values)
    except Exception as exc:
        summary["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        save(out, "summary", summary)
        (out / "terminal-last.txt").write_bytes(probe.output)
        try:
            probe.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
        inspector = factory()
        try:
            labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state)}"]
            assert not inspector.api.containers(all=True, filters={"label": labels}), "leaked training container"
            assert not inspector.api.networks(filters={"label": labels}), "leaked training network"
            assert not inspector.api.volumes(filters={"label": labels}).get("Volumes"), "leaked attempt volume"
        finally:
            inspector.close()
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
