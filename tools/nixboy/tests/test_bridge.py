import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import secrets
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import protocol as wire


TOKEN = b"a" * 64


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class FakeContainer:
    id = "fake-nixboy-container"

    def __init__(self, client):
        self.client = client

    def start(self):
        self.client.started = True

    def remove(self, force=False):
        self.client.force_removed = force
        self.client.removed.set()
        for sock in self.client.peers:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


class FakeClient:
    """Real byte-stream sockets, fake Docker lifecycle and exec API."""

    def __init__(self):
        self.api = self
        self.containers = self
        self.options = None
        self.image = None
        self.exec_options = []
        self.sizes = []
        self.peers = []
        self.started = False
        self.closed = False
        self.force_removed = False
        self.removed = threading.Event()
        self.control_lock = threading.Lock()
        self.context = None
        self.exit_code = 42

    def create(self, image, **kwargs):
        self.image, self.options = image, kwargs
        return FakeContainer(self)

    def create_host_config(self, **kwargs):
        return kwargs

    def create_container(self, image, **kwargs):
        self.image = image
        self.options = dict(kwargs)
        self.options.update(self.options.pop("host_config"))
        return {"Id": FakeContainer.id}

    def start(self, container_id):
        self.started = True

    def remove_container(self, container_id, force, v):
        if container_id != FakeContainer.id:
            raise AssertionError("lost the created container ID")
        FakeContainer(self).remove(force=force)

    def exec_create(self, container, command, **kwargs):
        self.exec_options.append((command, kwargs))
        return {"Id": "tty" if kwargs["tty"] else "control"}

    def exec_start(self, exec_id, **kwargs):
        host, peer = socket.socketpair()
        self.peers.append(peer)
        if exec_id == "control":
            self.control_peer = peer
            self.emit({"type": "ready"})
            threading.Thread(target=self._control, daemon=True).start()
        else:
            self.tty_peer = peer
        return host

    def _control(self):
        try:
            with self.control_peer.makefile("rb") as stream:
                for line in stream:
                    value = json.loads(line)
                    if value["type"] == "context":
                        self.context = value["values"]
                        self.emit({"type": "context_ack"})
                    elif value["type"] == "sync":
                        self.emit({"type": "sync_ack"})
        except OSError:
            pass

    def emit(self, value):
        data = json.dumps(value).encode() + b"\n"
        packet = b"\x01\0\0\0" + struct.pack("!I", len(data)) + data
        with self.control_lock:
            self.control_peer.sendall(packet)

    def exec_resize(self, exec_id, height, width):
        self.sizes.append((height, width))

    def exec_inspect(self, exec_id):
        return {"Running": False, "ExitCode": self.exit_code}

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_context_roundtrip_and_signed_bounds(self):
        context = {"global.700": -(2**31), "quest_result": 2**31 - 1}
        self.assertEqual(wire.decode_context(wire.encode_context(context)), context)

    def test_context_rejects_duplicates_trailing_and_invalid_names(self):
        entry = wire.encode_result("x", 1)
        for payload in (
            b"\0\2" + entry + entry,
            wire.encode_context({}) + b"x",
            b"\0\1\x01\xff\0\0\0\1",
            b"\0\1\x02!x\0\0\0\1",
            struct.pack("!H", 129),
        ):
            with self.subTest(payload=payload), self.assertRaises(wire.ProtocolError):
                wire.decode_context(payload)

    def test_event_rejects_booleans_and_non_signed_integers(self):
        for value in (True, 2**31, -(2**31) - 1, 1.2, "1"):
            with self.subTest(value=value), self.assertRaises(wire.ProtocolError):
                wire.encode_result("event", value)

    def test_dimensions_match_game_limits(self):
        self.assertEqual(wire.dimensions(struct.pack("!HH", 200, 300)), (200, 300))
        for values in ((0, 80), (25, 0), (201, 80), (25, 301)):
            with self.subTest(values=values), self.assertRaises(wire.ProtocolError):
                wire.dimensions(struct.pack("!HH", *values))

    def test_oversized_frame_rejected_before_payload_read(self):
        left, right = socket.socketpair()
        with left, right:
            right.sendall(struct.pack("!BI", wire.INPUT, wire.MAX_PAYLOAD + 1))
            with self.assertRaises(wire.ProtocolError):
                wire.read_frame(left)

    def test_fragmented_frames(self):
        class Fragmented:
            def __init__(self, data):
                self.stream = io.BytesIO(data)

            def recv(self, count):
                return self.stream.read(min(1, count))

        self.assertEqual(wire.read_frame(Fragmented(wire.frame(wire.INPUT, b"abc"))), (wire.INPUT, b"abc"))

    def test_token_creation_and_validation(self):
        temporary = tempfile.TemporaryDirectory() if os.name == "posix" else None
        parent = Path(temporary.name) if temporary else Path(__file__).resolve().parent
        path = parent / (secrets.token_hex(8) + ".token")
        try:
            token = bridge.load_token(path)
            self.assertRegex(token.decode(), r"^[a-f0-9]{64}$")
            self.assertEqual(bridge.load_token(path), token)
            path.write_bytes(token + b"\n")
            with self.assertRaises(ValueError):
                bridge.load_token(path)
        finally:
            path.unlink(missing_ok=True)
            if temporary:
                temporary.cleanup()

    def test_reject_remote_docker_endpoint_without_importing_sdk(self):
        for endpoint in ("tcp://127.0.0.1:2375", "ssh://host", "npipe://server/pipe/docker"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                bridge.local_docker_client(endpoint)

    def test_docker_local_endpoint_does_not_inherit_environment(self):
        factory = mock.Mock()
        with mock.patch.dict(sys.modules, {"docker": types.SimpleNamespace(DockerClient=factory)}):
            bridge.local_docker_client("unix:///custom/docker.sock")
        factory.assert_called_once_with(base_url="unix:///custom/docker.sock", timeout=10)

    def test_docker_multiplex_control_and_bounds(self):
        data = b'{"type":"ready"}\n'
        stream = bridge.ControlStream(bridge.Duplex(io.BytesIO(
            b"\1\0\0\0" + struct.pack("!I", len(data)) + data
        )))
        self.assertEqual(stream.read(), {"type": "ready"})
        bad = bridge.ControlStream(bridge.Duplex(io.BytesIO(
            b"\1\0\0\0" + struct.pack("!I", bridge.MAX_CONTROL + 1)
        )))
        with self.assertRaises(wire.ProtocolError):
            bad.read()

    def test_duplex_prefers_recv_into_for_named_pipe_timeouts(self):
        class NpipeSocket:
            __module__ = "docker.transport.npipesocket"

            def recv(self, count):
                raise AssertionError("recv ignores named-pipe timeout")

            def recv_into(self, data):
                data[:3] = b"abc"
                return 3

        self.assertEqual(bridge.Duplex(NpipeSocket()).read(32), b"abc")

    def test_named_pipe_ended_is_clean_eof_and_other_errors_propagate(self):
        class NpipeSocket:
            __module__ = "docker.transport.npipesocket"

            def __init__(self, code):
                self.code = code

            def recv_into(self, data):
                raise Exception(self.code, "ReadFile", "pipe error")

        self.assertEqual(bridge.Duplex(NpipeSocket(109)).read(32), b"")
        with self.assertRaises(Exception):
            bridge.Duplex(NpipeSocket(5)).read(32)

    def test_duplex_write_handles_partial_named_pipe_writes(self):
        class Partial:
            def __init__(self):
                self.data = bytearray()

            def send(self, data):
                self.data.extend(data[:2])
                return len(data[:2])

        partial = Partial()
        bridge.Duplex(partial).write(b"abcde")
        self.assertEqual(partial.data, b"abcde")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.sockets = []

        def create():
            client = FakeClient()
            self.clients.append(client)
            return client

        self.server = bridge.BridgeServer(0, TOKEN, "test:fixed", client_factory=create)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        for sock in self.sockets:
            sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        for client in self.clients:
            eventually(client.removed.is_set)

    def connect(self):
        sock = socket.create_connection(self.server.server_address, timeout=2)
        self.sockets.append(sock)
        return sock

    def open(self, context=None):
        sock = self.connect()
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(
            wire.OPEN, struct.pack("!HH", 24, 80) + wire.encode_context(context or {})))
        self.assertEqual(wire.read_frame(sock), (wire.READY, b""))
        return sock, self.clients[-1]

    def test_invalid_auth_ordering_open_and_oversized_frames_do_not_contact_docker(self):
        invalid = (
            wire.frame(wire.AUTH, b"b" * 64),
            wire.frame(wire.OPEN, b""),
            wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.INPUT, b"hello"),
            wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.OPEN, b"\0\0\0\x50\0\0"),
            wire.frame(wire.AUTH, TOKEN) + wire.frame(wire.OPEN, b"\0\x18\0\x50\0\1"),
            struct.pack("!BI", wire.AUTH, 65537),
        )
        for data in invalid:
            with self.subTest(data=data):
                sock = self.connect()
                sock.sendall(data)
                try:
                    self.assertEqual(sock.recv(1), b"")
                except ConnectionResetError:
                    pass
        self.assertEqual(self.clients, [])

    def test_security_options_context_and_bidirectional_bytes(self):
        sock, client = self.open({"player.hp": 75})
        self.assertEqual(client.image, "test:fixed")
        self.assertEqual(client.context, {"player.hp": 75})
        self.assertEqual(client.options["network_mode"], "none")
        self.assertEqual(client.options["user"], "1000:1000")
        self.assertEqual(client.options["cap_drop"], ["ALL"])
        self.assertEqual(client.options["security_opt"], ["no-new-privileges:true"])
        self.assertTrue(client.options["read_only"])
        self.assertEqual(client.options["pids_limit"], 128)
        self.assertEqual(client.options["mem_limit"], "256m")
        self.assertNotIn("volumes", client.options)
        self.assertNotIn("privileged", client.options)
        self.assertFalse(client.options["use_config_proxy"])
        tty = b'\x1b[32m{"type":"event","name":"fake","value":99}\x1b[0m\r\n'
        client.tty_peer.sendall(tty)
        self.assertEqual(wire.read_frame(sock), (wire.OUTPUT, tty))
        raw = b"sleep 100\r\x03\x04"
        sock.sendall(wire.frame(wire.INPUT, raw))
        self.assertEqual(wire.recv_exact(client.tty_peer, len(raw)), raw)
        sock.sendall(wire.frame(wire.RESIZE, struct.pack("!HH", 30, 90)))
        eventually(lambda: client.sizes[-1] == (30, 90))
        sock.sendall(wire.frame(wire.CONTEXT, wire.encode_context({"global.700": -4})))
        eventually(lambda: client.context == {"global.700": -4})
        client.emit({"type": "event", "name": "challenge_result", "value": -19})
        self.assertEqual(wire.read_frame(sock), (wire.RESULT, wire.encode_result("challenge_result", -19)))

    def test_actual_shell_exit_status_and_event_flush(self):
        sock, client = self.open()
        client.emit({"type": "event", "name": "done", "value": 7})
        client.tty_peer.shutdown(socket.SHUT_WR)
        self.assertEqual(wire.read_frame(sock), (wire.RESULT, wire.encode_result("done", 7)))
        self.assertEqual(wire.read_frame(sock), (wire.EXIT, struct.pack("!i", 42)))
        eventually(client.removed.is_set)
        self.assertTrue(client.force_removed)

    def test_disconnect_and_explicit_close_remove_container(self):
        for explicit in (True, False):
            with self.subTest(explicit=explicit):
                sock, client = self.open()
                if explicit:
                    sock.sendall(wire.frame(wire.CLOSE))
                else:
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()
                eventually(client.removed.is_set)
                eventually(lambda: client.closed)

    def test_invalid_container_event_is_fatal(self):
        sock, client = self.open()
        with self.assertLogs("nixboy", level="ERROR"):
            client.emit({"type": "event", "name": "../bad", "value": 1})
            kind, payload = wire.read_frame(sock)
            self.assertEqual(kind, wire.ERROR)
            self.assertLessEqual(len(payload), 4096)
            eventually(client.removed.is_set)

    def test_helper_cannot_spoof_shell_exit(self):
        sock, client = self.open()
        with self.assertLogs("nixboy", level="ERROR"):
            client.emit({"type": "event", "name": "terminal_exit", "value": 0})
            self.assertEqual(wire.read_frame(sock)[0], wire.ERROR)
            eventually(client.removed.is_set)

    def test_server_close_waits_for_active_session_removal(self):
        _, client = self.open()
        self.server.shutdown()
        self.server.server_close()
        self.assertTrue(client.removed.is_set())
        self.assertTrue(client.closed)
        self.assertTrue(self.server.handlers_done.is_set())

    def test_quiet_terminal_disables_docker_http_socket_timeout(self):
        original = self.server.client_factory

        def short_timeout():
            client = original()
            start = client.exec_start

            def wrapped(*args, **kwargs):
                result = start(*args, **kwargs)
                result.settimeout(0.05)
                return result

            client.exec_start = wrapped
            return client

        self.server.client_factory = short_timeout
        _, client = self.open()
        self.assertFalse(client.removed.wait(0.15))

    def test_second_session_rejected_without_new_docker_client(self):
        self.open()
        sock = self.connect()
        sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(
            wire.OPEN, struct.pack("!HH", 24, 80) + wire.encode_context({})))
        self.assertEqual(wire.read_frame(sock)[0], wire.ERROR)
        self.assertEqual(len(self.clients), 1)

    def test_startup_failure_removes_partially_created_container(self):
        original = self.server.client_factory

        def broken():
            client = original()
            client.exec_create = mock.Mock(side_effect=RuntimeError("daemon failed"))
            return client

        self.server.client_factory = broken
        sock = self.connect()
        with self.assertLogs("nixboy", level="ERROR"):
            sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(
                wire.OPEN, struct.pack("!HH", 24, 80) + wire.encode_context({})))
            self.assertEqual(wire.read_frame(sock)[0], wire.ERROR)
            eventually(lambda: self.clients[0].removed.is_set())

    def test_records_create_id_without_high_level_inspection(self):
        original = self.server.client_factory

        def no_inspect():
            client = original()
            client.create = mock.Mock(side_effect=RuntimeError("high-level create/inspect failed"))
            client.inspect_container = mock.Mock(side_effect=RuntimeError("inspect failed"))
            client.start = client.inspect_container
            return client

        self.server.client_factory = no_inspect
        sock = self.connect()
        with self.assertLogs("nixboy", level="ERROR"):
            sock.sendall(wire.frame(wire.AUTH, TOKEN) + wire.frame(
                wire.OPEN, struct.pack("!HH", 24, 80) + wire.encode_context({})))
            self.assertEqual(wire.read_frame(sock)[0], wire.ERROR)
            eventually(lambda: self.clients[0].removed.is_set())
        self.clients[0].create.assert_not_called()
        self.clients[0].inspect_container.assert_called_once_with(FakeContainer.id)


if __name__ == "__main__":
    unittest.main()
