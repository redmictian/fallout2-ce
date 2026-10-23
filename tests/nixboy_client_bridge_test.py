"""Exercise the real C++ client and Python bridge together without Docker.

Only Docker lifecycle/exec calls are faked. TCP framing, bridge session workers,
Docker multiplex control streams, context acknowledgements and the C++ parser
all run as shipped. No game assets, daemon, SDK package or image is required.
"""

from pathlib import Path
import json
import socket
import struct
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "nixboy"))
sys.path.insert(0, str(ROOT / "tools" / "nixboy" / "tests"))
import bridge
import protocol as wire
from test_bridge import FakeClient, eventually


def run(executable, scenario):
    clients = []

    def create():
        client = FakeClient()
        clients.append(client)
        return client

    server = bridge.BridgeServer(0, b"a" * 64, "test:fixed", client_factory=create)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    process = None
    try:
        process = subprocess.Popen(
            [executable, str(server.server_address[1]), scenario],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if scenario == "roundtrip":
            eventually(lambda: clients and hasattr(clients[0], "tty_peer"))
            client = clients[0]
            initial = json.loads(client.exec_options[-1][1]["environment"]["NIXBOY_CONTEXT_JSON"])
            assert initial == {"player.hp": 2**31 - 1, "global.minimum": -(2**31)}
            client.tty_peer.settimeout(2)
            expected = b"command\0\r\x03"
            actual = bytearray()
            while len(actual) < len(expected):
                chunk = client.tty_peer.recv(len(expected) - len(actual))
                assert chunk, "C++ input stream closed early"
                actual.extend(chunk)
            assert actual == expected, "binary INPUT differs across languages"
            eventually(lambda: client.sizes and client.sizes[-1] == (30, 100))
            eventually(lambda: client.context == {"quest.updated": -17})
            assert client.options["network_mode"] == "none"
            client.tty_peer.sendall(b"Linux \0\x1b[32mready\r\n")
            client.emit({"type": "event", "name": "minimum", "value": -(2**31)})
            client.emit({"type": "event", "name": "maximum", "value": 2**31 - 1})
            client.tty_peer.shutdown(socket.SHUT_WR)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, f"{scenario}: {stderr} {stdout}"
        if scenario == "bad-auth":
            assert clients == [], "bad authentication contacted Docker"
        else:
            assert len(clients) == 1
            eventually(clients[0].removed.is_set)
            eventually(lambda: clients[0].closed)
            assert clients[0].force_removed
        print(stdout.strip())
    finally:
        if process and process.poll() is None:
            process.kill()
            process.communicate(timeout=2)
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def run_training(executable):
    """Use the shipped Python codec as a peer for the real C++ v4 client."""
    process = None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        try:
            process = subprocess.Popen([executable, str(listener.getsockname()[1]), "training"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                assert wire.read_frame(connection) == (wire.AUTH, b"a" * 64)
                kind, payload = wire.read_frame(connection)
                assert kind == wire.TRAIN_OPEN
                rows, cols, context, claims = wire.decode_training_open(payload)
                assert (rows, cols) == (24, 80)
                identity, skills = wire.training_context(context)
                assert identity == "00000001000000000000000000000000"
                assert skills == dict.fromkeys(range(18), 15)
                assert claims == {"3:2": {"receipt": "00" * 15 + "01", "points": 5},
                                  "13:300": {"receipt": "00" * 15 + "02", "points": 5}}
                connection.sendall(wire.frame(wire.READY))
                assert wire.read_frame(connection) == (wire.TRAIN_ACTION, bytes([wire.TRAIN_CHECK]))
                reward = wire.encode_training_reward(13, 65535, 7, bytes(15) + b"\x25")
                connection.sendall(wire.frame(wire.TRAIN_REWARD, reward))
                kind, payload = wire.read_frame(connection)
                assert kind == wire.TRAIN_ACK
                assert wire.decode_training_ack(payload) == (13, 65535, 7, "00" * 15 + "25", True)
                kind, payload = wire.read_frame(connection)
                assert kind == wire.CONTEXT
                _, skills = wire.training_context(wire.decode_context(payload))
                assert skills[13] == 22
                connection.sendall(wire.frame(wire.EXIT, struct.pack("!i", 42)))
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, f"training: {stderr} {stdout}"
            print(stdout.strip())
        finally:
            if process and process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: nixboy_client_bridge_test.py <client-test-executable>")
    for scenario in ("roundtrip", "close", "bad-auth"):
        run(sys.argv[1], scenario)
    run_training(sys.argv[1])
