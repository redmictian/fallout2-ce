"""Nix-Boy wire format, deliberately independent of Docker and the game."""

import re
import struct

MAX_PAYLOAD = 65536
MAX_CONTEXT = 128
AUTH, OPEN, INPUT, RESIZE, CLOSE, CONTEXT = range(1, 7)
TRAIN_OPEN, TRAIN_ACTION, TRAIN_ACK = range(7, 10)
READY, OUTPUT, RESULT, EXIT, ERROR = range(16, 21)
TRAIN_REWARD = 21
TRAIN_CHECK, TRAIN_HUB, TRAIN_RESET, TRAIN_ABANDON = range(1, 5)
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
TOKEN = re.compile(rb"[0-9a-fA-F]{64}\Z")


class ProtocolError(ValueError):
    pass


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError("connection closed")
        data.extend(chunk)
    return bytes(data)


def read_frame(sock):
    kind, size = struct.unpack("!BI", recv_exact(sock, 5))
    if size > MAX_PAYLOAD:
        raise ProtocolError("frame too large")
    return kind, recv_exact(sock, size)


def frame(kind, payload=b""):
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("frame too large")
    return struct.pack("!BI", kind, len(payload)) + payload


def dimensions(payload):
    if len(payload) != 4:
        raise ProtocolError("invalid terminal size")
    rows, cols = struct.unpack("!HH", payload)
    if not 1 <= rows <= 200 or not 1 <= cols <= 300:
        raise ProtocolError("terminal size outside supported range")
    return rows, cols


def validate_event(name, value):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ProtocolError("invalid event name")
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ProtocolError("value outside signed 32-bit range")


def encode_result(name, value):
    validate_event(name, value)
    encoded = name.encode("ascii")
    return bytes([len(encoded)]) + encoded + struct.pack("!i", value)


def encode_context(values):
    if len(values) > MAX_CONTEXT:
        raise ProtocolError("too many context entries")
    return struct.pack("!H", len(values)) + b"".join(
        encode_result(name, value) for name, value in values.items()
    )


def decode_context(payload):
    if len(payload) < 2:
        raise ProtocolError("missing context count")
    count = struct.unpack_from("!H", payload)[0]
    if count > MAX_CONTEXT:
        raise ProtocolError("too many context entries")
    values = {}
    offset = 2
    for _ in range(count):
        if offset >= len(payload):
            raise ProtocolError("truncated context")
        size = payload[offset]
        offset += 1
        if not 1 <= size <= 64 or offset + size + 4 > len(payload):
            raise ProtocolError("invalid context entry")
        try:
            name = payload[offset : offset + size].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError("non-ASCII name") from exc
        offset += size
        value = struct.unpack_from("!i", payload, offset)[0]
        offset += 4
        validate_event(name, value)
        if name in values:
            raise ProtocolError("duplicate context name")
        values[name] = value
    if offset != len(payload):
        raise ProtocolError("trailing context bytes")
    return values


def training_context(values):
    """Validate the training handshake before catalog/state/Docker access."""
    if values.get("training.version") != 1:
        raise ProtocolError("unsupported training version")
    chunks = []
    for index in range(4):
        value = values.get(f"training.profile.{index}")
        if type(value) is not int or not -(2**31) <= value < 2**31:
            raise ProtocolError("missing training profile")
        chunks.append(struct.pack("!I", value & 0xffffffff))
    profile = b"".join(chunks)
    if profile == bytes(16):
        raise ProtocolError("invalid training profile")
    completed = {}
    for skill, name in ((3, "unarmed"), (12, "science")):
        level = values.get(f"training.completed.{name}")
        if type(level) is not int or not 0 <= level <= 2:
            raise ProtocolError("invalid training progress")
        completed[skill] = level
    return profile.hex(), completed


def encode_training_reward(skill, level, receipt):
    if skill not in (3, 12) or level not in (1, 2) or len(receipt) != 16 or not any(receipt):
        raise ProtocolError("invalid training reward")
    return bytes((skill, level)) + receipt


def decode_training_ack(payload):
    if len(payload) != 19 or payload[0] not in (3, 12) or payload[1] not in (1, 2) \
            or not any(payload[2:18]) or payload[18] not in (0, 1):
        raise ProtocolError("invalid training acknowledgment")
    return payload[0], payload[1], payload[2:18].hex(), bool(payload[18])
