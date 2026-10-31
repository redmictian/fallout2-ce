"""Nix-Boy wire format, deliberately independent of Docker and the game."""

import re
import struct

MAX_PAYLOAD = 65536
MAX_CONTEXT = 128
AUTH, OPEN, INPUT, RESIZE, CLOSE, CONTEXT = range(1, 7)
TRAIN_OPEN, TRAIN_ACTION, TRAIN_ACK = range(7, 10)
TRAIN_PERK_ACK = 10
TRAIN_CAPTURE = 11
READY, OUTPUT, RESULT, EXIT, ERROR = range(16, 21)
TRAIN_REWARD = 21
TRAIN_PERK_REWARD = 22
TRAIN_SNAPSHOT = 23
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
MAX_PERK_CLAIMS = 64
PILOT_PERK = 12
PILOT_PERK_RANK = 2


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
    if values.get("training.version") not in (4, 5, 6):
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
    """Validate immutable sparse claims with points and receipt, independent of content."""
    if not isinstance(claims, dict) or len(claims) > MAX_CLAIMS:
        raise ProtocolError("invalid or oversized training claim ledger")
    seen = set()
    for key, claim in claims.items():
        if not isinstance(key, str) or len(key) > 8 or not re.fullmatch(r"(?:0|[1-9][0-9]*):[1-9][0-9]*", key):
            raise ProtocolError("invalid training claim key")
        skill, level = map(int, key.split(":"))
        if not isinstance(claim, dict) or set(claim) != {"receipt", "points"}:
            raise ProtocolError("invalid training claim fields")
        receipt, points = claim["receipt"], claim["points"]
        if not 0 <= skill < len(SKILL_NAMES) or not 1 <= level <= MAX_LEVEL \
                or type(points) is not int or not 1 <= points <= 300 \
                or not isinstance(receipt, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt) \
                or receipt == "0" * 32 or receipt in seen:
            raise ProtocolError("invalid or duplicate training receipt")
        seen.add(receipt)
    return claims


def validate_perk_claims(claims):
    """Typed pilot claims; no skill fields or invented upgrade amounts."""
    if not isinstance(claims, dict) or len(claims) > MAX_PERK_CLAIMS:
        raise ProtocolError("invalid or oversized perk claim ledger")
    seen = set()
    for key, receipt in claims.items():
        if key != f"{PILOT_PERK}:{PILOT_PERK_RANK}" or not isinstance(receipt, str) \
                or not re.fullmatch(r"[0-9a-f]{32}", receipt) or receipt == "0" * 32 or receipt in seen:
            raise ProtocolError("invalid or duplicate perk training receipt")
        seen.add(receipt)
    return claims


def validate_progression_binding(binding):
    if not isinstance(binding, dict) or set(binding) != {"activation", "snapshot", "restore"} \
            or not isinstance(binding["activation"], str) \
            or not re.fullmatch(r"[0-9a-f]{32}", binding["activation"]) \
            or binding["activation"] == "0" * 32 \
            or not isinstance(binding["snapshot"], str) \
            or not re.fullmatch(r"[0-9a-f]{64}", binding["snapshot"]) \
            or type(binding["restore"]) is not bool:
        raise ProtocolError("invalid save progression binding")
    return binding


def fence_training_payload(payload, activation):
    if not isinstance(activation, str) or not re.fullmatch(r"[0-9a-f]{32}", activation) \
            or activation == "0" * 32:
        raise ProtocolError("invalid progression activation")
    if len(payload) + 16 > MAX_PAYLOAD:
        raise ProtocolError("bound training payload exceeds frame limit")
    return bytes.fromhex(activation) + payload


def unfence_training_payload(payload, activation):
    prefix = fence_training_payload(b"", activation)
    if len(payload) < 16 or payload[:16] != prefix:
        raise ProtocolError("stale progression delivery or acknowledgment")
    return payload[16:]


def encode_training_open(rows, cols, values, claims, perk_claims=None, binding=None):
    size = struct.pack("!HH", rows, cols)
    dimensions(size)
    training_context(values)
    validate_claims(claims)
    context = encode_context(values)
    records = b"".join(encode_training_reward(*map(int, key.split(":")), claim["points"], bytes.fromhex(claim["receipt"]))
                       for key, claim in sorted(claims.items()))
    payload = size + struct.pack("!I", len(context)) + context + struct.pack("!H", len(claims)) + records
    perk_claims = {} if perk_claims is None else perk_claims
    validate_perk_claims(perk_claims)
    if any(receipt in {claim["receipt"] for claim in claims.values()} for receipt in perk_claims.values()):
        raise ProtocolError("receipt reused across reward types")
    if values["training.version"] >= 5:
        payload += struct.pack("!H", len(perk_claims))
        for key, receipt in sorted(perk_claims.items()):
            perk, rank = map(int, key.split(":"))
            payload += struct.pack("!HB", perk, rank) + bytes.fromhex(receipt)
    elif perk_claims:
        raise ProtocolError("perk claims require training version 5")
    if values["training.version"] == 6:
        validate_progression_binding(binding)
        payload += bytes.fromhex(binding["activation"]) + bytes.fromhex(binding["snapshot"]) + bytes([binding["restore"]])
    elif binding is not None:
        raise ProtocolError("save progression binding requires training version 6")
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("training open exceeds frame limit")
    return payload


def decode_training_open(payload, include_perks=False, include_binding=False):
    if len(payload) < 12:
        raise ProtocolError("truncated training open")
    rows, cols = dimensions(payload[:4])
    size = struct.unpack_from("!I", payload, 4)[0]
    if size < 2 or 8 + size + 2 > len(payload):
        raise ProtocolError("invalid training context size")
    values = decode_context(payload[8:8 + size])
    training_context(values)
    count = struct.unpack_from("!H", payload, 8 + size)[0]
    skill_end = 10 + size + count * 21
    if count > MAX_CLAIMS or (values["training.version"] == 4 and len(payload) != skill_end) \
            or (values["training.version"] >= 5 and len(payload) < skill_end + 2):
        raise ProtocolError("invalid training claim record count")
    claims = {}
    offset = 10 + size
    for _ in range(count):
        skill, level, points = struct.unpack_from("!BHH", payload, offset)
        receipt = payload[offset + 5:offset + 21].hex()
        key = f"{skill}:{level}"
        if key in claims:
            raise ProtocolError("duplicate training claim key")
        claims[key] = {"receipt": receipt, "points": points}
        offset += 21
    validate_claims(claims)
    perks = {}
    if values["training.version"] >= 5:
        perk_count = struct.unpack_from("!H", payload, offset)[0]
        offset += 2
        tail = 49 if values["training.version"] == 6 else 0
        if perk_count > MAX_PERK_CLAIMS or len(payload) != offset + perk_count * 19 + tail:
            raise ProtocolError("invalid perk training record count")
        for _ in range(perk_count):
            perk, rank = struct.unpack_from("!HB", payload, offset)
            key = f"{perk}:{rank}"
            if key in perks:
                raise ProtocolError("duplicate perk training claim key")
            perks[key] = payload[offset + 3:offset + 19].hex()
            offset += 19
        validate_perk_claims(perks)
        if any(receipt in {claim["receipt"] for claim in claims.values()} for receipt in perks.values()):
            raise ProtocolError("receipt reused across reward types")
    binding = None
    if values["training.version"] == 6:
        if payload[offset + 48] not in (0, 1):
            raise ProtocolError("invalid restore selector")
        binding = validate_progression_binding({"activation": payload[offset:offset+16].hex(),
            "snapshot": payload[offset+16:offset+48].hex(), "restore": bool(payload[offset+48])})
    if include_binding:
        return rows, cols, values, claims, perks, binding
    return (rows, cols, values, claims, perks) if include_perks else (rows, cols, values, claims)


def encode_training_reward(skill, level, points, receipt):
    if type(skill) is not int or not 0 <= skill < len(SKILL_NAMES) \
            or type(level) is not int or not 1 <= level <= MAX_LEVEL \
            or type(points) is not int or not 1 <= points <= 300 \
            or len(receipt) != 16 or not any(receipt):
        raise ProtocolError("invalid training reward")
    return struct.pack("!BHH", skill, level, points) + receipt


def decode_training_ack(payload):
    if len(payload) != 22:
        raise ProtocolError("invalid training acknowledgment")
    skill, level, points = struct.unpack_from("!BHH", payload)
    encode_training_reward(skill, level, points, payload[5:21])
    if payload[21] not in (0, 1):
        raise ProtocolError("invalid training acknowledgment")
    return skill, level, points, payload[5:21].hex(), bool(payload[21])


def encode_training_perk_reward(profile, perk, rank, receipt):
    if isinstance(profile, str):
        if not re.fullmatch(r"[0-9a-f]{32}", profile):
            raise ProtocolError("invalid perk reward profile")
        profile = bytes.fromhex(profile)
    if not isinstance(profile, bytes) or len(profile) != 16 or not any(profile) \
            or type(perk) is not int or perk != PILOT_PERK or type(rank) is not int or rank != PILOT_PERK_RANK \
            or not isinstance(receipt, bytes) or len(receipt) != 16 or not any(receipt):
        raise ProtocolError("invalid typed perk reward")
    return profile + struct.pack("!HB", perk, rank) + receipt


def decode_training_perk_reward(payload):
    if len(payload) != 35:
        raise ProtocolError("invalid typed perk reward length")
    perk, rank = struct.unpack_from("!HB", payload, 16)
    encode_training_perk_reward(payload[:16], perk, rank, payload[19:35])
    return payload[:16].hex(), perk, rank, payload[19:35].hex()


def decode_training_perk_ack(payload):
    if len(payload) != 36 or payload[35] not in (0, 1):
        raise ProtocolError("invalid perk acknowledgment")
    return (*decode_training_perk_reward(payload[:35]), bool(payload[35]))
