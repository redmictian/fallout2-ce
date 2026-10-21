import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import protocol


def load_helper(request):
    name = "nixboy_helper_test"
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / "container" / "nixboy"))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"control": types.SimpleNamespace(request=request)}):
        loader.exec_module(module)
    return module


class HelperTests(unittest.TestCase):
    def test_run_emits_actual_exit_and_signal_status(self):
        for raw, expected in ((7, 7), (0, 0), (-2, 130)):
            request = mock.Mock(return_value={"ok": True})
            helper = load_helper(request)
            with mock.patch.object(sys, "argv", ["nixboy", "run", "task", "bash", "-c", "exit 7"]):
                with mock.patch.object(helper.subprocess, "run", return_value=types.SimpleNamespace(returncode=raw)) as run:
                    self.assertEqual(helper.main(), expected)
            run.assert_called_once_with(["bash", "-c", "exit 7"], check=False)
            request.assert_called_once_with({"type": "emit", "name": "task", "value": expected})

    def test_run_missing_command_emits_127(self):
        request = mock.Mock(return_value={"ok": True})
        helper = load_helper(request)
        with mock.patch.object(sys, "argv", ["nixboy", "run", "task", "missing"]):
            with mock.patch.object(helper.subprocess, "run", side_effect=FileNotFoundError):
                self.assertEqual(helper.main(), 127)
        request.assert_called_once_with({"type": "emit", "name": "task", "value": 127})

    def test_reserved_exit_name_and_integer_range_rejected_before_command(self):
        for argv in (["run", "terminal_exit", "true"], ["emit", "event", str(2**31)]):
            request = mock.Mock()
            helper = load_helper(request)
            with mock.patch.object(sys, "argv", ["nixboy"] + argv), mock.patch("sys.stderr", io.StringIO()):
                with mock.patch.object(helper.subprocess, "run") as run:
                    self.assertEqual(helper.main(), 125)
            request.assert_not_called()
            run.assert_not_called()

    def test_context_queries_live_socket(self):
        request = mock.Mock(return_value={"values": {"player.hp": 47}})
        helper = load_helper(request)
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["nixboy", "context", "player.hp"]), mock.patch("sys.stdout", output):
            self.assertEqual(helper.main(), 0)
        self.assertEqual(output.getvalue(), "47\n")
        request.assert_called_once_with({"type": "context"})


@unittest.skipUnless(os.name == "posix", "container control server uses Linux Unix sockets")
class ControlSocketTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("nixboy_control_test", HERE / "container" / "control.py")
        self.control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.control)
        self.temporary = tempfile.TemporaryDirectory(prefix="nixboy-test-")
        directory = Path(self.temporary.name)
        self.control.DIRECTORY = directory
        self.control.SOCKET = str(directory / "control.sock")
        self.control.CONTEXT = directory / "context.json"
        self.server = self.control.Server()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.sock = self.control.connect()
        self.stream = self.sock.makefile("rb")
        self.sock.sendall(self.control.encode({"type": "subscribe"}))
        self.assertEqual(self.control.read_line(self.stream), {"type": "ready"})

    def tearDown(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.stream.close()
        self.sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temporary.cleanup()

    def test_live_context_atomic_file_event_and_sync(self):
        self.sock.sendall(self.control.encode({"type": "context", "values": {"player.hp": 73}}))
        self.assertEqual(self.control.read_line(self.stream), {"type": "context_ack"})
        self.assertEqual(json.loads(self.control.CONTEXT.read_text()), {"player.hp": 73})
        self.assertEqual(self.control.request({"type": "context"}), {"values": {"player.hp": 73}})
        self.assertEqual(self.control.request({"type": "emit", "name": "task", "value": -19}), {"ok": True})
        self.assertEqual(self.control.read_line(self.stream), {"type": "event", "name": "task", "value": -19})
        self.sock.sendall(self.control.encode({"type": "sync"}))
        self.assertEqual(self.control.read_line(self.stream), {"type": "sync_ack"})

    def test_invalid_helper_events_rejected_without_publishing(self):
        for value in (
            {"type": "emit", "name": "terminal_exit", "value": 0},
            {"type": "emit", "name": "bad/name", "value": 1},
            {"type": "emit", "name": "task", "value": True},
            {"type": "emit", "name": "task", "value": 2**31},
        ):
            with self.subTest(value=value), self.assertRaises(protocol.ProtocolError):
                self.control.request(value)
        self.sock.sendall(self.control.encode({"type": "sync"}))
        self.assertEqual(self.control.read_line(self.stream), {"type": "sync_ack"})


if __name__ == "__main__":
    unittest.main()
