#!/usr/bin/env python3
"""Opt-in real training socket -> Compose -> PTY -> validation -> receipt smoke.

This is a test client, not Fallout. It models the game's level/receipt contract;
actual skill rendering and SAVE.DAT integration require game acceptance.
"""

import argparse
import base64
import importlib.util
import json
from pathlib import Path
import re
import secrets
import shlex
import socket
import struct
import tempfile
import threading
import time

from bridge import BridgeServer, local_docker_client
from challenge_env import OWNER_LABEL, TRAINING_LABEL, owner_namespace
from protocol import (
    AUTH, CLOSE, CONTEXT, ERROR, INPUT, OUTPUT, READY, TRAIN_OPEN, TRAIN_ACTION,
    TRAIN_ACK, TRAIN_REWARD, TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET,
    SKILL_NAMES, decode_training_ack, encode_context, encode_training_open, frame, read_frame,
)
from training import AttemptManager


class Probe:
    def __init__(self, server, token, manager, skill_values=None):
        self.server, self.token, self.manager = server, token, manager
        self.profile = secrets.token_bytes(16)
        self.identity = self.profile.hex()
        self.skill_values = dict.fromkeys(range(18), 25)
        self.skill_values.update({3: 15, 12: 10})
        self.skill_values.update(skill_values or {})
        self.receipts = {}
        self.socket = None
        self.output = bytearray()
        self.rewards = []

    def progress(self):
        return {f"{skill}:{level}": dict(claim) for (skill, level), claim in self.receipts.items()}

    def context(self):
        values = {"training.version": 4}
        values.update({"skill." + name: self.skill_values[skill] for skill, name in enumerate(SKILL_NAMES)})
        for index, value in enumerate(struct.unpack("!4i", self.profile)):
            values[f"training.profile.{index}"] = value
        return values

    def connect(self):
        if not self.server.handlers_done.wait(5):
            raise AssertionError("previous frontend did not detach")
        self.socket = socket.create_connection(self.server.server_address, timeout=5)
        self.output.clear()
        self.rewards.clear()
        self.socket.sendall(frame(AUTH, self.token) + frame(
            TRAIN_OPEN, encode_training_open(35, 139, self.context(), self.progress())))
        self.until(lambda kind, data: kind == READY)
        self.until_output(b"Choose a number")

    def packet(self, deadline):
        probe = self
        class DeadlineSocket:
            def recv(self, count):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("training smoke deadline exceeded")
                probe.socket.settimeout(remaining)
                return probe.socket.recv(count)
        kind, payload = read_frame(DeadlineSocket())
        if kind == ERROR:
            raise AssertionError(payload.decode("utf-8", "replace"))
        if kind == OUTPUT:
            self.output.extend(payload)
            if len(self.output) > 2 * 1024 * 1024:
                raise AssertionError("training smoke output budget exceeded")
        elif kind == TRAIN_REWARD:
            skill, level, points, receipt_hex, _ = decode_training_ack(payload + b"\1")
            key, receipt = (skill, level), {"receipt": receipt_hex, "points": points}
            if key in self.receipts:
                if self.receipts[key] != receipt:
                    raise AssertionError(f"changed receipt for completed level {key}")
                # Match the real game's idempotent duplicate behavior. A retry
                # can already be in flight when ACK/context crosses the socket.
                self.socket.sendall(frame(TRAIN_ACK, payload + b"\1")
                                    + frame(CONTEXT, encode_context(self.context())))
                return None, b""
            self.rewards.append(payload)
        elif kind != READY:
            raise AssertionError(f"unexpected training frame {kind}")
        return kind, payload

    def until(self, predicate, seconds=70):
        deadline = time.monotonic() + seconds
        while True:
            kind, payload = self.packet(deadline)
            if predicate(kind, payload):
                return payload

    def until_output(self, marker, seconds=70):
        return self.until(lambda kind, data: kind == OUTPUT and marker in self.output, seconds)

    def input(self, data):
        self.output.clear()
        self.socket.sendall(frame(INPUT, data))

    def action(self, action):
        self.output.clear()
        self.socket.sendall(frame(TRAIN_ACTION, bytes([action])))

    def start(self, skill, practice=False):
        number = list(self.manager.catalog.tracks).index(skill) + 1
        self.input(str(number).encode() + b"\r" + (b"p\r" if practice else b"a\r"))
        self.until_output(b"challenge:~$ ")
        return self.manager.snapshot(self.identity)["attempt"]

    def command(self, argv):
        marker = secrets.token_hex(12).encode("ascii")
        escaped = "".join("\\%03o" % byte for byte in marker)
        command = shlex.join(argv) + "; rc=$?; printf '" + escaped + ":%d\\n' \"$rc\"\n"
        self.input(command.encode("utf-8"))
        self.until_output(marker)
        match = re.search(re.escape(marker) + rb":([0-9]+)\r?\n", self.output)
        if not match:
            # Marker and its status can arrive in separate OUTPUT frames.
            self.until(lambda kind, data: bool(re.search(re.escape(marker) + rb":([0-9]+)\r?\n", self.output)))
            match = re.search(re.escape(marker) + rb":([0-9]+)\r?\n", self.output)
        code = int(match.group(1))
        output = bytes(self.output[:match.start()]).decode("utf-8", "replace")
        return code, output, ""

    def reward(self):
        while True:
            if not self.rewards:
                self.until(lambda kind, data: kind == TRAIN_REWARD)
            payload = self.rewards.pop(0)
            skill, level, points, receipt_hex, _ = decode_training_ack(payload + b"\1")
            if self.receipts.get((skill, level)) == {"receipt": receipt_hex, "points": points}:
                self.socket.sendall(frame(TRAIN_ACK, payload + b"\1")
                                    + frame(CONTEXT, encode_context(self.context())))
                continue
            return payload

    def accept(self, payload):
        skill, level, points, receipt_hex, _ = decode_training_ack(payload + b"\1")
        if (skill, level) in self.receipts:
            raise AssertionError("repeated reward")
        self.receipts[skill, level] = {"receipt": receipt_hex, "points": points}
        self.skill_values[skill] += points
        self.socket.sendall(frame(TRAIN_ACK, payload + b"\1") + frame(CONTEXT, encode_context(self.context())))

    def close(self, explicit=True):
        if self.socket:
            try:
                if explicit:
                    self.socket.sendall(frame(CLOSE))
            except OSError:
                pass
            finally:
                self.socket.close()
                self.socket = None
        if not self.server.handlers_done.wait(5):
            raise AssertionError("frontend did not finish detach")


class ShellSolution:
    """Route the separate reference solver through player-visible PTY INPUT."""

    def __init__(self, probe, attempt, catalog):
        self.probe = probe
        self.params, self.spec = attempt["params"], catalog.get(attempt["challenge"])

    def _exec(self, service, argv, user="1000:1000"):
        if service != "workstation" or user != "1000:1000":
            raise AssertionError("reference solution attempted to bypass the workstation shell")
        return self.probe.command(argv)

    def _write(self, service, path, text, mode=0o644):
        if service != "workstation":
            raise AssertionError("reference solution attempted direct server writes")
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        code = "import base64,os,sys; p=sys.argv[1]; open(p,'wb').write(base64.b64decode(sys.argv[2])); os.chmod(p,int(sys.argv[3]))"
        status, output, error = self.probe.command(["python3", "-c", code, path, encoded, str(mode)])
        if status:
            raise AssertionError((status, output, error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="authorize disposable real Docker training exercises")
    parser.add_argument("--one", action="store_true", help="limit the live progression check to one rewarded exercise")
    parser.add_argument("--content-dir", type=Path, help="external content catalog to exercise")
    parser.add_argument("--skill", type=int, default=12, help="Fallout skill ID for --one (default Science=12)")
    parser.add_argument("--skill-value", type=int, help="current skill value for --one (default 10)")
    parser.add_argument("--expected-level", type=int, default=1, help="expected authored level for --one")
    args = parser.parse_args()
    if not 0 <= args.skill < 18 or args.skill_value is not None and not 0 <= args.skill_value <= 300:
        parser.error("invalid skill or skill value")
    if not 1 <= args.expected_level <= 65535:
        parser.error("invalid expected level")
    if not args.run:
        parser.error("pass --run to create and remove disposable training environments")
    loader = importlib.util.spec_from_file_location("training_reference", Path(__file__).parent / "tests/training_reference.py")
    reference = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(reference)
    factory = local_docker_client
    checks = []
    with tempfile.TemporaryDirectory(prefix="nixboy-training-smoke-") as state:
        from challenge_content import Catalog
        manager = AttemptManager(state, factory, catalog=Catalog(args.content_dir))
        token = secrets.token_hex(32).encode("ascii")
        server = BridgeServer(0, token, "fallout2-nixboy:dev", client_factory=factory, training_manager=manager)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        probe = Probe(server, token, manager,
                      {args.skill: 10 if args.skill_value is None else args.skill_value} if args.one else None)
        def bash_count():
            inspector = factory()
            try:
                environment = manager.environments[probe.identity]
                processes = inspector.api.top(environment.container_id, ps_args="-eo pid,comm")["Processes"]
                return sum(row[1] == "bash" for row in processes)
            finally:
                inspector.close()
        try:
            probe.connect()
            if not args.one:
                # An unsolved explicit check rotates exactly one shuffled pool entry.
                attempt = probe.start(3)
                before = manager.snapshot(probe.identity)["queues"]["3:1"]
                probe.action(TRAIN_CHECK)
                probe.until_output(b"Incomplete.")
                after = manager.snapshot(probe.identity)["queues"]["3:1"]
                assert after == before[1:] + before[:1]
                assert not manager.pending_rewards(probe.identity, probe.progress())
                checks.append("incomplete-rotates")

                # The practice path exercises the same state validator without rewards.
                attempt = probe.start(3, practice=True)
                reference.solve(ShellSolution(probe, attempt, manager.catalog))
                probe.action(TRAIN_CHECK)
                probe.until_output(b"Practice completed; no reward")
                assert not manager.pending_rewards(probe.identity, probe.progress())
                checks.append("practice-no-reward")

            first_skill = args.skill if args.one else 3
            first_level = args.expected_level if args.one else 1
            for skill in ((args.skill,) if args.one else (3, 12)):
                for level in ((first_level,) if args.one else (1, 2)):
                    attempt = probe.start(skill)
                    assert attempt["level"] == level
                    if skill == first_skill and level == first_level:
                        status, _, _ = probe.command(["touch", "/home/nixboy/reconnect-marker"])
                        assert status == 0
                        for _ in range(3):
                            probe.action(TRAIN_HUB)
                            probe.until_output(b"Choose a number")
                            assert bash_count() == 0, "hub detach leaked a Bash process"
                            probe.input(b"r\r")
                            probe.until_output(b"challenge:~$ ")
                            assert bash_count() == 1, "resume must attach exactly one Bash"
                        probe.close()
                        assert bash_count() == 0, "socket detach leaked a Bash process"
                        probe.connect()
                        probe.input(b"r\r")
                        probe.until_output(b"challenge:~$ ")
                        assert bash_count() == 1
                        assert probe.command(["test", "-f", "/home/nixboy/reconnect-marker"])[0] == 0
                        probe.action(TRAIN_RESET)
                        probe.until_output(b"challenge:~$ ")
                        rebuilt = manager.snapshot(probe.identity)["attempt"]
                        assert rebuilt["seed"] == attempt["seed"] and rebuilt["params"] == attempt["params"]
                        assert rebuilt["generation"] == attempt["generation"] + 1
                        assert probe.command(["test", "-e", "/home/nixboy/reconnect-marker"])[0] == 1
                        checks.append("detach-resume-reset")
                        checks.append("repeated-attachments-leave-no-idle-bash")
                    reference.solve(ShellSolution(probe, attempt, manager.catalog))
                    probe.action(TRAIN_CHECK)
                    probe.until_output(b"Skill reward earned")
                    reward = probe.reward()
                    assert struct.unpack("!BH", reward[:3]) == (skill, level), {
                        "expected": [skill, level], "actual": list(struct.unpack("!BH", reward[:3])),
                        "completed": probe.progress(), "receipt": reward[5:].hex(),
                    }
                    if skill == first_skill and level == first_level:
                        probe.close(explicit=False)  # Lost acknowledgment.
                        probe.connect()
                        assert probe.reward() == reward
                        checks.append("lost-ack-replays-exact-receipt")
                    probe.accept(reward)
                    completed = sum(skill_id == skill for skill_id, _ in probe.receipts)
                    probe.until_output(f"completed {completed}/".encode())
                    check = {"skill": skill, "level": level, "challenge": attempt["challenge"], "reward_points": attempt["reward_points"], "status": "passed"}
                    checks.append(check)
                    print(json.dumps(check), flush=True)
                    if args.one:
                        break
                if args.one:
                    break
            assert len(probe.progress()) == (1 if args.one else 4)
            assert len({claim["receipt"] for claim in probe.receipts.values()}) == (1 if args.one else 4)
            probe.close()
            probe.connect()
            assert not manager.pending_rewards(probe.identity, probe.progress())
            assert manager.snapshot(probe.identity)["attempt"] is None
            checks.append("completed-tracks-reconnect-without-pending-rewards")
        finally:
            try:
                probe.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)
        inspector = factory()
        try:
            labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state)}"]
            assert not inspector.api.containers(all=True, filters={"label": labels}), "leaked training containers"
            assert not inspector.api.networks(filters={"label": labels}), "leaked training networks"
        finally:
            inspector.close()
    print(json.dumps({"status": "passed", "checks": checks}), flush=True)


if __name__ == "__main__":
    main()
