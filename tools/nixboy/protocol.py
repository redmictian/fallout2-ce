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
SKILL_NAMES = (
    "small_guns", "big_guns", "energy_weapons", "unarmed", "melee_weapons", "throwing",
    "first_aid", "doctor", "sneak", "lockpick", "steal", "traps", "science", "repair",
    "speech", "barter", "gambling", "outdoorsman",
)
MAX_CLAIMS = 2048
MAX_LEVEL = 65535


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
    if values.get("training.version") != 3:
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
    skills = {}
    for skill, name in enumerate(SKILL_NAMES):
        value = values.get(f"skill.{name}")
        if type(value) is not int or not -(2**31) <= value < 2**31:
            raise ProtocolError("missing current training skill value")
        skills[skill] = value
    return profile.hex(), skills


def validate_claims(claims):
    """Validate a sparse skill:level -> receipt-hex ledger, independent of content."""
    if not isinstance(claims, dict) or len(claims) > MAX_CLAIMS:
        raise ProtocolError("invalid or oversized training claim ledger")
    seen = set()
    for key, receipt in claims.items():
        if not isinstance(key, str) or len(key) > 8 or not re.fullmatch(r"(?:0|[1-9][0-9]*):[1-9][0-9]*", key):
            raise ProtocolError("invalid training claim key")
        skill, level = map(int, key.split(":"))
        if not 0 <= skill < len(SKILL_NAMES) or not 1 <= level <= MAX_LEVEL \
                or not isinstance(receipt, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt) \
                or receipt == "0" * 32 or receipt in seen:
            raise ProtocolError("invalid or duplicate training receipt")
        seen.add(receipt)
    return claims


def encode_training_open(rows, cols, values, claims):
    size = struct.pack("!HH", rows, cols)
    dimensions(size)
    training_context(values)
    validate_claims(claims)
    context = encode_context(values)
    records = b"".join(encode_training_reward(*map(int, key.split(":")), bytes.fromhex(receipt))
                       for key, receipt in sorted(claims.items()))
    payload = size + struct.pack("!I", len(context)) + context + struct.pack("!H", len(claims)) + records
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("training open exceeds frame limit")
    return payload


def decode_training_open(payload):
    if len(payload) < 12:
        raise ProtocolError("truncated training open")
    rows, cols = dimensions(payload[:4])
    size = struct.unpack_from("!I", payload, 4)[0]
    if size < 2 or 8 + size + 2 > len(payload):
        raise ProtocolError("invalid training context size")
    values = decode_context(payload[8:8 + size])
    training_context(values)
    count = struct.unpack_from("!H", payload, 8 + size)[0]
    if count > MAX_CLAIMS or len(payload) != 10 + size + count * 19:
        raise ProtocolError("invalid training claim record count")
    claims = {}
    offset = 10 + size
    for _ in range(count):
        skill, level = struct.unpack_from("!BH", payload, offset)
        receipt = payload[offset + 3:offset + 19].hex()
        key = f"{skill}:{level}"
        if key in claims:
            raise ProtocolError("duplicate training claim key")
        claims[key] = receipt
        offset += 19
    validate_claims(claims)
    return rows, cols, values, claims


def encode_training_reward(skill, level, receipt):
    if type(skill) is not int or not 0 <= skill < len(SKILL_NAMES) \
            or type(level) is not int or not 1 <= level <= MAX_LEVEL \
            or len(receipt) != 16 or not any(receipt):
        raise ProtocolError("invalid training reward")
    return struct.pack("!BH", skill, level) + receipt


def decode_training_ack(payload):
    if len(payload) != 20:
        raise ProtocolError("invalid training acknowledgment")
    skill, level = struct.unpack_from("!BH", payload)
    encode_training_reward(skill, level, payload[3:19])
    if payload[19] not in (0, 1):
        raise ProtocolError("invalid training acknowledgment")
    return skill, level, payload[3:19].hex(), bool(payload[19])
