#!/usr/bin/env python3
"""Read-only inspection of the v4 training trailer in a disposable SAVE.DAT.

This extracts the actual game's saved profile and earned points for acceptance
evidence. It does not load the game, parse unrelated save payloads or modify files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from protocol import MAX_CLAIMS, SKILL_NAMES, validate_claims

MAGIC = b"NXBTRAIN"


def inspect(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("save exceeds the bounded inspection size")
    data = path.read_bytes()
    offset = data.rfind(MAGIC)
    if offset < 0:
        raise ValueError("no training trailer found")
    trailer = data[offset:]
    if not 38 <= len(trailer) <= 38 + MAX_CLAIMS * 21:
        raise ValueError("invalid training trailer size")
    version, payload = struct.unpack_from("!II", trailer, 8)
    if version != 4 or payload != len(trailer) - 20:
        raise ValueError("inspection requires the exact v4 training trailer")
    checksum, = struct.unpack_from("!I", trailer, len(trailer) - 4)
    if checksum != zlib.crc32(trailer[:-4]):
        raise ValueError("training trailer checksum mismatch")
    count, = struct.unpack_from("!H", trailer, 32)
    if count > MAX_CLAIMS or len(trailer) != 38 + count * 21:
        raise ValueError("training claim length/count mismatch")
    claims = {}
    for index in range(count):
        start = 34 + index * 21
        skill, level, points = struct.unpack_from("!BHH", trailer, start)
        key = f"{skill}:{level}"
        if key in claims:
            raise ValueError("duplicate saved training claim")
        claims[key] = {"receipt": trailer[start+5:start+21].hex(), "points": points}
    validate_claims(claims)
    profile = trailer[16:32].hex()
    if count and int(profile, 16) == 0:
        raise ValueError("earned claims require a nonzero profile")
    bonuses = {name: sum(c["points"] for key, c in claims.items() if int(key.split(":")[0]) == skill)
               for skill, name in enumerate(SKILL_NAMES)}
    return {"path": str(path.resolve()), "save_sha256": hashlib.sha256(data).hexdigest(),
            "training_offset": offset, "training_bytes": len(trailer), "version": version,
            "crc32": f"{checksum:08x}", "profile": profile, "claims": claims,
            "bonuses": {key: value for key, value in bonuses.items() if value},
            "scope": "Actual saved training ledger; effective skill also depends on ordinary game state."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.save), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
