#!/usr/bin/env python3
"""Container-only control socket and Docker exec relay; never reads PTY output."""

import json
import os
from pathlib import Path
import socket
import socketserver
import sys
import threading
import time

from protocol import MAX_CONTEXT, ProtocolError, validate_event

DIRECTORY = Path("/tmp/nixboy")
SOCKET = str(DIRECTORY / "control.sock")
CONTEXT = DIRECTORY / "context.json"
MAX_LINE = 32768


def encode(value):
    data = json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
    if len(data) > MAX_LINE:
        raise ProtocolError("control record too large")
    return data


def read_line(stream):
    line = stream.readline(MAX_LINE + 1)
    if not line:
        raise EOFError("control connection closed")
    if len(line) > MAX_LINE or not line.endswith(b"\n"):
        raise ProtocolError("invalid control line")
    result = json.loads(line)
    if not isinstance(result, dict):
        raise ProtocolError("control record must be an object")
    return result


def validate_context(values):
    if not isinstance(values, dict) or len(values) > MAX_CONTEXT:
        raise ProtocolError("invalid context")
    for name, value in values.items():
        validate_event(name, value)


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.subscriber_lock = threading.Lock()
        self.subscriber = None
        self.values = {}

    def set_context(self, values):
        validate_context(values)
        with self.lock:
            temporary = CONTEXT.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, separators=(",", ":")), encoding="ascii")
            os.replace(temporary, CONTEXT)
            self.values = dict(values)

    def publish(self, value):
        with self.subscriber_lock:
            if self.subscriber is None:
                raise ProtocolError("no game control connection")
            self.subscriber.sendall(encode(value))


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        state = self.server.state
        self.request.settimeout(5)
        subscriber = False
        try:
            value = read_line(self.rfile)
            if value == {"type": "subscribe"}:
                with state.subscriber_lock:
                    if state.subscriber is not None:
                        raise ProtocolError("game already connected")
                    state.subscriber = self.request
                    subscriber = True
                    self.request.sendall(encode({"type": "ready"}))
                self.request.settimeout(None)
                while True:
                    value = read_line(self.rfile)
                    if set(value) == {"type", "values"} and value["type"] == "context":
                        state.set_context(value["values"])
                        state.publish({"type": "context_ack"})
                    elif value == {"type": "sync"}:
                        state.publish({"type": "sync_ack"})
                    else:
                        raise ProtocolError("invalid game control request")
            elif value == {"type": "context"}:
                with state.lock:
                    self.request.sendall(encode({"values": state.values}))
            elif set(value) == {"type", "name", "value"} and value["type"] == "emit":
                validate_event(value["name"], value["value"])
                if value["name"] == "terminal_exit":
                    raise ProtocolError("terminal_exit is reserved for the actual shell exit")
                state.publish({"type": "event", "name": value["name"], "value": value["value"]})
                self.request.sendall(encode({"ok": True}))
            else:
                raise ProtocolError("invalid helper request")
        except (EOFError, OSError, ValueError, UnicodeError) as exc:
            if not subscriber:
                try:
                    self.request.sendall(encode({"error": str(exc)[:200]}))
                except OSError:
                    pass
        finally:
            if subscriber:
                with state.subscriber_lock:
                    if state.subscriber is self.request:
                        state.subscriber = None


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self):
        DIRECTORY.mkdir(mode=0o700, exist_ok=True)
        self.state = State()
        self.state.set_context({})
        super().__init__(SOCKET, Handler)
        os.chmod(SOCKET, 0o600)


def connect():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(SOCKET)
    return sock


def request(value):
    with connect() as sock:
        sock.sendall(encode(value))
        with sock.makefile("rb") as stream:
            result = read_line(stream)
    if "error" in result:
        raise ProtocolError(result["error"])
    return result


def relay():
    # Docker exec can be attached before the container's PID 1 has bound its
    # Unix socket. Retry for a bounded interval; do not emit chatter on stdout.
    deadline = time.monotonic() + 4
    while True:
        try:
            sock = connect()
            break
        except (FileNotFoundError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.025)
    sock.settimeout(None)
    sock.sendall(encode({"type": "subscribe"}))

    def forward_output():
        try:
            with sock.makefile("rb") as stream:
                while True:
                    # Validate bounds on both directions, stdout contains only
                    # records from the separate control socket.
                    sys.stdout.buffer.write(encode(read_line(stream)))
                    sys.stdout.buffer.flush()
        except (OSError, EOFError, ValueError):
            pass
        finally:
            # Main may be blocked in stdin; exit the relay when its peer dies.
            os._exit(0)

    threading.Thread(target=forward_output, daemon=True).start()
    try:
        while True:
            sock.sendall(encode(read_line(sys.stdin.buffer)))
    except (OSError, EOFError, ValueError):
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        with Server() as server:
            server.serve_forever()
    elif sys.argv[1:] == ["relay"]:
        relay()
    else:
        raise SystemExit("usage: control.py serve|relay")
