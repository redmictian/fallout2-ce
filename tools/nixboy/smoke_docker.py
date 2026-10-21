#!/usr/bin/env python3
"""Opt-in real Docker/PTY integration smoke; never installs or starts Docker."""

import argparse
import contextlib
import io
import secrets
import socket
import struct
import threading
import time

from bridge import BridgeServer, local_docker_client
from protocol import AUTH, CLOSE, CONTEXT, ERROR, EXIT, INPUT, OPEN, OUTPUT, READY, RESIZE, RESULT
from protocol import encode_context, encode_result, frame, read_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="authorize creating ephemeral test containers")
    parser.add_argument("--image", default="fallout2-nixboy:dev")
    parser.add_argument("--docker-endpoint")
    parser.add_argument("--idle-seconds", default=12, type=float,
                        help="quiet-session check (default exceeds the Docker HTTP timeout)")
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to execute the real-Docker smoke test")
    if not 0 <= args.idle_seconds <= 60:
        parser.error("idle-seconds must be 0..60")
    import docker

    token = secrets.token_hex(32).encode("ascii")
    factory = lambda: local_docker_client(args.docker_endpoint)
    inspector = factory()
    inspector.ping()
    if inspector.info().get("OSType") != "linux":
        raise RuntimeError("the daemon must run Linux containers")
    inspector.images.get(args.image)  # Never pull an image implicitly.
    server = BridgeServer(0, token, args.image, client_factory=factory)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    sock = None

    def connect():
        if not server.handlers_done.wait(5):
            raise AssertionError("previous session did not finish cleanup")
        connection = socket.create_connection(server.server_address, timeout=20)
        connection.sendall(frame(AUTH, token) + frame(
            OPEN, struct.pack("!HH", 24, 80) + encode_context({"player.hp": 73})))
        kind, payload = read_frame(connection)
        if kind != READY:
            raise AssertionError((kind, payload))
        with server.handlers_lock:
            container_id = next(iter(server.sessions)).container.id
        return connection, container_id

    def receive_until(kind, expected=None):
        output = bytearray()
        results = []
        deadline = time.monotonic() + 20

        class DeadlineSocket:
            def recv(self, count):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("smoke receive deadline exceeded")
                sock.settimeout(remaining)
                return sock.recv(count)

        while True:
            packet_kind, payload = read_frame(DeadlineSocket())
            if packet_kind == ERROR:
                raise AssertionError(payload.decode("utf-8", "replace"))
            if packet_kind == OUTPUT:
                output.extend(payload)
            elif packet_kind == RESULT:
                results.append(payload)
            if len(output) > 1024 * 1024 or len(results) > 128:
                raise AssertionError("smoke response exceeds output/event budget")
            if packet_kind == EXIT and kind != EXIT:
                raise AssertionError("shell exited before the expected response")
            if packet_kind == kind and (expected is None or expected in payload):
                return bytes(output), results, payload
            if kind == OUTPUT and expected is not None and expected in output:
                return bytes(output), results, payload

    def command(command_text):
        # The marker is emitted through octal escapes, so echoed command text
        # cannot be mistaken for the actual command-completion marker.
        marker = secrets.token_hex(12).encode("ascii")
        escaped = "".join("\\%03o" % byte for byte in marker)
        sock.sendall(frame(INPUT, (command_text + "; printf '" + escaped + "\\n'\n").encode("ascii")))
        output, results, _ = receive_until(OUTPUT, marker)
        return output, results

    def removed(container_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                inspector.containers.get(container_id)
            except docker.errors.NotFound:
                return
            time.sleep(0.05)
        raise AssertionError("container was not removed: " + container_id)

    try:
        sock, first_id = connect()
        actual = inspector.containers.get(first_id).attrs
        assert actual["Config"]["User"] == "1000:1000", actual["Config"]["User"]
        assert actual["HostConfig"]["ReadonlyRootfs"] is True
        assert actual["HostConfig"]["NetworkMode"] == "none"
        assert actual["HostConfig"]["CapDrop"] == ["ALL"]
        assert actual["HostConfig"]["Memory"] == 256 * 1024 * 1024
        assert not actual["HostConfig"].get("Binds")
        time.sleep(args.idle_seconds)
        output, _ = command("stty size; nixboy context player.hp; id -u")
        assert b"24 80\r\n" in output, output
        assert b"\r\n73\r\n" in output, output
        assert b"\r\n1000\r\n" in output, output
        sock.sendall(frame(RESIZE, struct.pack("!HH", 31, 92)))
        sock.sendall(frame(CONTEXT, encode_context({"player.hp": 47})))
        output, _ = command("stty size; nixboy context player.hp")
        assert b"31 92\r\n" in output, output
        assert b"\r\n47\r\n" in output, output
        sock.sendall(frame(INPUT, b"sleep 30\n"))
        time.sleep(0.15)
        sock.sendall(frame(INPUT, b"\x03"))
        command("true")  # The prompt must recover from SIGINT before timeout.
        sock.sendall(frame(INPUT, b"nixboy emit challenge_result -19\n"))
        receive_until(RESULT, encode_result("challenge_result", -19))
        sock.sendall(frame(INPUT, b"nixboy run task_result bash -c 'exit 7'\n"))
        receive_until(RESULT, encode_result("task_result", 7))
        # Event delivery must finish before the final shell EXIT frame.
        sock.sendall(frame(INPUT, b"nixboy emit before_exit 9; exit 23\n"))
        _, results, payload = receive_until(EXIT)
        assert encode_result("before_exit", 9) in results, results
        assert struct.unpack("!i", payload)[0] == 23, payload
        sock.close()
        sock = None
        removed(first_id)
        deadline = time.monotonic() + 5
        while not server.handlers_done.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        sock, second_id = connect()
        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
        sock = None
        removed(second_id)

        sock, close_id = connect()
        sock.sendall(frame(CLOSE))
        sock.close()
        sock = None
        removed(close_id)

        # Repeated use must release the active-session slot and all containers.
        for status in (0, 7, 42):
            sock, repeat_id = connect()
            sock.sendall(frame(INPUT, f"exit {status}\n".encode("ascii")))
            _, _, payload = receive_until(EXIT)
            assert struct.unpack("!i", payload)[0] == status, payload
            sock.close()
            sock = None
            removed(repeat_id)

        if not server.handlers_done.wait(5):
            raise AssertionError("cleanup did not release failed-startup test slot")
        server.image = "fallout2-nixboy-smoke-missing:" + secrets.token_hex(12)
        # Docker create must fail without pulling; retain expected log noise only
        # for an assertion failure, then prove the same server can recover.
        with contextlib.redirect_stderr(io.StringIO()):
            sock = socket.create_connection(server.server_address, timeout=20)
            sock.sendall(frame(AUTH, token) + frame(
                OPEN, struct.pack("!HH", 24, 80) + encode_context({})))
            kind, _ = read_frame(sock)
            assert kind == ERROR, kind
            sock.close()
            sock = None
            if not server.handlers_done.wait(5):
                raise AssertionError("failed image startup did not finish")
        server.image = args.image

        sock, shutdown_id = connect()
        server.shutdown()
        server.server_close()
        # The first Bash prompt may already be queued before shutdown. Drain
        # that finite output and require EOF rather than discarding its bytes.
        sock.settimeout(5)
        drained = 0
        deadline = time.monotonic() + 5
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("active shutdown did not close game connection")
            sock.settimeout(remaining)
            data = sock.recv(16384)
            if not data:
                break
            drained += len(data)
            if drained > 1024 * 1024:
                raise AssertionError("shutdown output exceeds smoke budget")
        sock.close()
        sock = None
        removed(shutdown_id)
        print("PASS: real Linux PTY, isolation, idle recovery, Ctrl+C, resize, live context, results, exits, repeated sessions, failed-start recovery and close/disconnect/shutdown cleanup")
    finally:
        if sock:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        inspector.close()


if __name__ == "__main__":
    main()
