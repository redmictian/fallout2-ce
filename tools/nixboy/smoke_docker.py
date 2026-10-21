#!/usr/bin/env python3
"""Opt-in real Docker/PTY integration smoke; never installs or starts Docker."""

import argparse
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
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to execute the real-Docker smoke test")
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
        while True:
            packet_kind, payload = read_frame(sock)
            if packet_kind == ERROR:
                raise AssertionError(payload.decode("utf-8", "replace"))
            if packet_kind == OUTPUT:
                output.extend(payload)
            elif packet_kind == RESULT:
                results.append(payload)
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
        output, _ = command("stty size; nixboy context player.hp")
        assert b"24 80\r\n" in output, output
        assert b"\r\n73\r\n" in output, output
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
        print("PASS: real Linux PTY output/input, Ctrl+C, resize, live context, events, shell exit and disconnect cleanup")
    finally:
        if sock:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        inspector.close()


if __name__ == "__main__":
    main()
