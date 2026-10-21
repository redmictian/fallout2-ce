#!/usr/bin/env python3
"""Authenticated local game socket -> ephemeral Docker Linux terminal.

The Docker SDK is imported only when a valid authenticated OPEN starts a session.
No client-supplied host command, image, path, environment name, or mount is used.
"""

import argparse
import hmac
import json
import logging
import os
from pathlib import Path
import queue
import secrets
import select
import socket
import socketserver
import struct
import threading
import time

from protocol import (
    AUTH, CLOSE, CONTEXT, ERROR, EXIT, INPUT, OPEN, OUTPUT, READY, RESIZE, RESULT,
    MAX_PAYLOAD, TOKEN, ProtocolError, decode_context, dimensions, encode_result,
    frame, read_frame, validate_event,
)

LOG = logging.getLogger("nixboy")
MAX_CONTROL = 32768
DOCKER_TIMEOUT = 10


def load_token(path):
    """Create a private token once; never log or accept whitespace around it."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_hex(32).encode("ascii"))
    token = path.read_bytes()
    if not TOKEN.fullmatch(token):
        raise ValueError("token file must contain exactly 64 ASCII hex characters")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("token file permissions must exclude group and other access")
    return token


def local_docker_client(endpoint=None):
    # Do not inherit DOCKER_HOST, TLS configuration, or remote Docker contexts.
    endpoint = endpoint or (
        "npipe:////./pipe/docker_engine" if os.name == "nt"
        else "unix:///var/run/docker.sock"
    )
    if not endpoint.startswith(("unix:///", "npipe:////./pipe/")):
        raise ValueError("only a local Docker Unix socket or Windows named pipe is supported")
    import docker
    return docker.DockerClient(base_url=endpoint, timeout=DOCKER_TIMEOUT)


class Duplex:
    """Normalize Docker's socket/SocketIO/named-pipe wrappers without fileno()."""

    def __init__(self, stream):
        self.stream = stream
        self.socket = getattr(stream, "_sock", stream)
        self.is_npipe = type(self.socket).__module__ == "docker.transport.npipesocket"
        self.write_lock = threading.Lock()

    def timeout(self, value):
        if hasattr(self.socket, "settimeout"):
            self.socket.settimeout(value)

    def read(self, count):
        try:
            # docker-py NpipeSocket.recv uses a synchronous ReadFile that ignores
            # settimeout; recv_into uses the bounded overlapped operation.
            if hasattr(self.socket, "recv_into"):
                data = bytearray(count)
                size = self.socket.recv_into(data)
                return bytes(data[:size])
            if hasattr(self.stream, "read"):
                return self.stream.read(count)
            return self.socket.recv(count)
        except Exception as exc:
            if self.is_npipe and exc.args and exc.args[0] == 109:
                return b""  # Windows ERROR_BROKEN_PIPE is ordinary attach EOF.
            raise

    def exact(self, count):
        result = bytearray()
        while len(result) < count:
            chunk = self.read(count - len(result))
            if not chunk:
                raise EOFError("Docker stream closed")
            result.extend(chunk)
        return bytes(result)

    def write(self, data):
        with self.write_lock:
            if hasattr(self.socket, "send"):
                view = memoryview(data)
                while view:
                    written = self.socket.send(view)
                    if not written:
                        raise EOFError("Docker stream write failed")
                    view = view[written:]
            else:
                view = memoryview(data)
                while view:
                    written = self.stream.write(view)
                    if not written:
                        raise EOFError("Docker stream write failed")
                    view = view[written:]
                if hasattr(self.stream, "flush"):
                    self.stream.flush()

    def close(self):
        if self.is_npipe:
            try:
                self.socket.close()
            except Exception:
                pass
        else:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except (AttributeError, OSError):
                pass
        # docker-py keeps the owning requests response on the raw stream.
        # Close that owner before its SocketIO; Python 3.14 HTTPResponse.close
        # flushes the buffered file and otherwise sees an already-closed raw
        # file during finalization. Shutdown above first wakes blocked readers.
        response = getattr(self.stream, "_response", None)
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        try:
            self.stream.close()
        except OSError:
            pass


class ControlStream:
    """Decode Docker non-TTY multiplex framing, then bounded JSON lines."""

    def __init__(self, duplex):
        self.duplex = duplex
        self.pending = bytearray()

    def send(self, value):
        data = json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n"
        if len(data) > MAX_CONTROL:
            raise ProtocolError("control record too large")
        self.duplex.write(data)

    def read(self):
        while b"\n" not in self.pending:
            header = self.duplex.exact(8)
            stream, size = header[0], struct.unpack_from("!I", header, 4)[0]
            if header[1:4] != b"\0\0\0" or stream != 1 or size > MAX_CONTROL:
                raise ProtocolError("invalid Docker control stream")
            if not size:
                continue
            self.pending.extend(self.duplex.exact(size))
            if len(self.pending) > MAX_CONTROL:
                raise ProtocolError("control record too large")
        line, _, remaining = self.pending.partition(b"\n")
        self.pending = bytearray(remaining)
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ProtocolError("invalid control JSON") from exc
        if not isinstance(value, dict):
            raise ProtocolError("control record must be an object")
        return value


class DockerSession:
    def __init__(self, client, image, network, send, connected=lambda: None):
        self.client = client
        self.image = image
        self.network = network
        self.send = send
        self.connected = connected
        self.container = None
        self.tty = None
        self.control = None
        self.exec_id = None
        self.closed = threading.Event()
        self.close_lock = threading.Lock()
        self.threads = []
        self.synced = threading.Event()
        self.context_acked = threading.Event()
        self.requests = queue.Queue(maxsize=16)

    def start(self, rows, cols, context):
        self.connected()
        host_config = self.client.api.create_host_config(
            network_mode=self.network, read_only=True,
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
            mem_limit="256m", memswap_limit="256m", pids_limit=128,
            cpu_period=100000, cpu_quota=100000,
            tmpfs={
                "/tmp": "rw,noexec,nosuid,size=16m,mode=1777",
                "/home/nixboy": "rw,nosuid,size=64m,uid=1000,gid=1000,mode=0700",
            },
        )
        created = self.client.api.create_container(
            image=self.image,
            command=["python3", "-u", "/opt/nixboy/control.py", "serve"],
            user="1000:1000", working_dir="/home/nixboy",
            detach=True, stdin_open=False, tty=False, host_config=host_config,
            environment={"TERM": "xterm-256color", "HOME": "/home/nixboy",
                         "NIXBOY_CONTEXT_PATH": "/tmp/nixboy/context.json"},
            labels={"org.fallout2-ce.nixboy": "session"}, use_config_proxy=False,
        )
        # The high-level containers.create performs an extra inspect before
        # returning its model. Record the ID before any follow-up API request so
        # even a failed start/inspect can remove the already created container.
        self.container = ContainerRef(self.client.api, created["Id"])
        self.connected()
        self.container.start()
        self.connected()
        control_exec = self.client.api.exec_create(
            self.container.id,
            ["python3", "-u", "/opt/nixboy/control.py", "relay"],
            stdin=True, stdout=True, stderr=False, tty=False, user="1000:1000",
        )["Id"]
        self.control = ControlStream(Duplex(self.client.api.exec_start(
            control_exec, tty=False, socket=True,
        )))
        self.control.duplex.timeout(5)
        if self.control.read() != {"type": "ready"}:
            raise ProtocolError("container control agent did not become ready")
        self.control.send({"type": "context", "values": context})
        if self.control.read() != {"type": "context_ack"}:
            raise ProtocolError("container rejected initial context")
        self.control.duplex.timeout(None)
        self.connected()
        self.exec_id = self.client.api.exec_create(
            self.container.id, ["/bin/bash", "--noprofile", "--norc", "-i"],
            stdin=True, stdout=True, stderr=True, tty=True, user="1000:1000",
            environment={"NIXBOY_CONTEXT_JSON": json.dumps(context, separators=(",", ":")),
                         "PS1": "nix-boy:\\w$ "},
        )["Id"]
        self.tty = Duplex(self.client.api.exec_start(self.exec_id, tty=True, socket=True))
        self.tty.timeout(None)  # A quiet shell must outlive the HTTP API timeout.
        self.client.api.exec_resize(self.exec_id, height=rows, width=cols)
        self.connected()
        self.send(READY)
        # Start readers only after READY: Bash can produce its first prompt at once.
        for worker in (self._output, self._events, self._requests):
            thread = threading.Thread(target=self._guard, args=(worker,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _guard(self, worker):
        try:
            worker()
        except Exception:
            if not self.closed.is_set():
                LOG.exception("container session stream failed")
                try:
                    self.send(ERROR, b"Linux terminal disconnected; see bridge log")
                except OSError:
                    pass
        finally:
            self.close()

    def _output(self):
        while not self.closed.is_set():
            data = self.tty.read(16384)
            if not data:
                break
            self.send(OUTPUT, data)
        if self.closed.is_set():
            return
        # A helper emit acknowledged inside Linux must reach the game before
        # EXIT closes the connection, even though the two exec streams race.
        self.control.send({"type": "sync"})
        if not self.synced.wait(5):
            raise ProtocolError("container event flush timed out")
        # EOF can precede Docker updating exec state; never guess a zero exit code.
        deadline = time.monotonic() + 5
        while True:
            state = self.client.api.exec_inspect(self.exec_id)
            if not state.get("Running"):
                code = state.get("ExitCode")
                if type(code) is not int or not -(2**31) <= code < 2**31:
                    raise ProtocolError("Docker did not provide a shell exit code")
                self.send(EXIT, struct.pack("!i", code))
                return
            if time.monotonic() >= deadline:
                raise ProtocolError("Docker did not finish the shell exec")
            self.closed.wait(0.05)

    def _events(self):
        while not self.closed.is_set():
            value = self.control.read()
            if value == {"type": "context_ack"}:
                self.context_acked.set()
                continue
            if value == {"type": "sync_ack"}:
                self.synced.set()
                continue
            if set(value) != {"type", "name", "value"} or value["type"] != "event":
                raise ProtocolError("invalid container event")
            validate_event(value["name"], value["value"])
            if value["name"] == "terminal_exit":
                raise ProtocolError("terminal_exit is reserved for the actual shell exit")
            self.send(RESULT, encode_result(value["name"], value["value"]))

    def input(self, data):
        self._enqueue(("input", data))

    def resize(self, rows, cols):
        self._enqueue(("resize", (rows, cols)))

    def context(self, values):
        self._enqueue(("context", values))

    def _enqueue(self, item):
        try:
            self.requests.put_nowait(item)
        except queue.Full as exc:
            raise ProtocolError("container input queue is full") from exc

    def _requests(self):
        # A stalled PTY must not prevent the handler observing game disconnect.
        while not self.closed.is_set():
            try:
                kind, value = self.requests.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "input":
                self.tty.write(value)
            elif kind == "resize":
                rows, cols = value
                self.client.api.exec_resize(self.exec_id, height=rows, width=cols)
            else:
                self.context_acked.clear()
                self.control.send({"type": "context", "values": value})
                if not self.context_acked.wait(5):
                    raise ProtocolError("container context update timed out")

    def close(self):
        with self.close_lock:
            if self.closed.is_set():
                return
            self.closed.set()
            for stream in (self.tty, self.control.duplex if self.control else None):
                if stream:
                    stream.close()
            if self.container:
                try:
                    self.container.remove(force=True)
                except Exception:
                    # Daemon loss can make removal impossible. The labeled ID is
                    # recorded for explicit operator cleanup after Docker recovers.
                    LOG.exception("could not remove Nix-Boy container %s", self.container.id)
            try:
                self.client.close()
            except Exception:
                LOG.exception("could not close Docker client")


class ContainerRef:
    def __init__(self, api, container_id):
        self.api, self.id = api, container_id

    def start(self):
        self.api.start(self.id)

    def remove(self, force):
        self.api.remove_container(self.id, force=force, v=True)


class BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.session = None
        self.send_lock = threading.Lock()
        self.request.settimeout(5)
        acquired = False
        try:
            kind, payload = read_frame(self.request)
            if kind != AUTH or not TOKEN.fullmatch(payload) or not hmac.compare_digest(
                payload, self.server.token
            ):
                raise ProtocolError("authentication failed")
            kind, payload = read_frame(self.request)
            if kind != OPEN or len(payload) < 6:
                raise ProtocolError("expected OPEN after AUTH")
            rows, cols = dimensions(payload[:4])
            context = decode_context(payload[4:])
            acquired = self.server.active.acquire(blocking=False)
            if not acquired:
                self.send(ERROR, b"Nix-Boy already has an active session")
                return
            self.session = DockerSession(
                self.server.client_factory(), self.server.image, self.server.network,
                self.send, self.check_connected,
            )
            with self.server.handlers_lock:
                self.server.sessions.add(self.session)
            self.session.start(rows, cols, context)
            self.request.settimeout(1)
            while not self.session.closed.is_set() and not self.server.stopping.is_set():
                # A timeout inside a partial frame is fatal: do not resume at the
                # middle of a header. Idle sockets are handled before read_frame.
                ready, _, _ = select.select([self.request], [], [], 0.5)
                if not ready:
                    continue
                kind, payload = read_frame(self.request)
                if kind == INPUT and payload:
                    self.session.input(payload)
                elif kind == RESIZE:
                    self.session.resize(*dimensions(payload))
                elif kind == CONTEXT:
                    self.session.context(decode_context(payload))
                elif kind == CLOSE and not payload:
                    break
                else:
                    raise ProtocolError("invalid request type, ordering or payload")
        except (EOFError, ProtocolError, ConnectionError, socket.timeout):
            pass
        except Exception:
            LOG.exception("Nix-Boy session failed")
            try:
                self.send(ERROR, b"Cannot start or continue Linux terminal; see bridge log")
            except OSError:
                pass
        finally:
            if self.session:
                self.session.close()
                with self.server.handlers_lock:
                    self.server.sessions.discard(self.session)
            if acquired:
                self.server.active.release()

    def check_connected(self):
        if self.server.stopping.is_set():
            raise EOFError("bridge shutting down")
        ready, _, _ = select.select([self.request], [], [], 0)
        if ready:
            # The game must wait for READY. Readable here means disconnect or
            # an out-of-order frame; either cancels further startup operations.
            raise EOFError("game disconnected or sent data before READY")

    def send(self, kind, payload=b""):
        if kind == ERROR:
            payload = payload[:4096]
        with self.send_lock:
            self.request.sendall(frame(kind, payload))


class BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 4

    def __init__(self, port, token, image, network="none", client_factory=local_docker_client):
        self.token, self.image, self.network = token, image, network
        self.client_factory = client_factory
        self.active = threading.BoundedSemaphore(1)
        self.connection_slots = threading.BoundedSemaphore(8)
        self.handlers = set()
        self.sessions = set()
        self.handlers_lock = threading.Lock()
        self.handlers_done = threading.Event()
        self.handlers_done.set()
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", port), BridgeHandler)

    def process_request(self, request, address):
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.handlers_lock:
            self.handlers.add(request)
            self.handlers_done.clear()
        try:
            super().process_request(request, address)
        except Exception:
            self._release(request)
            raise

    def _release(self, request):
        with self.handlers_lock:
            self.handlers.discard(request)
            if not self.handlers:
                self.handlers_done.set()
        self.connection_slots.release()

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._release(request)

    def server_close(self):
        self.stopping.set()
        with self.handlers_lock:
            requests = list(self.handlers)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()
        # Handler threads are daemons so an unresponsive daemon cannot keep the
        # process alive forever. Normal Ctrl+C still waits for removal, including
        # any in-flight startup API call followed by force-remove.
        if not self.handlers_done.wait(DOCKER_TIMEOUT * 3 + 5):
            with self.handlers_lock:
                pending = [session.container.id if session.container else "pending startup"
                           for session in self.sessions]
            LOG.error("shutdown cleanup timed out; inspect Nix-Boy containers: %s", pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--port", default=47821, type=int)
    parser.add_argument("--image", default="fallout2-nixboy:dev")
    parser.add_argument("--network", choices=("none", "bridge"), default="none")
    parser.add_argument("--docker-endpoint", help="local unix:/// or npipe:////./pipe/ endpoint")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    if args.docker_endpoint and not args.docker_endpoint.startswith(("unix:///", "npipe:////./pipe/")):
        parser.error("Docker endpoint must be a local Unix socket or Windows named pipe")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = load_token(args.token_file)
    factory = lambda: local_docker_client(args.docker_endpoint)
    with BridgeServer(args.port, token, args.image, args.network, factory) as server:
        LOG.info("Nix-Boy listening on 127.0.0.1:%d (network=%s)", args.port, args.network)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            LOG.info("Nix-Boy shutting down")


if __name__ == "__main__":
    main()
