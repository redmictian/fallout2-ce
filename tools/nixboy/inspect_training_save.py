#!/usr/bin/env python3
"""Read-only inspection of v4/v5 training trailers in disposable SAVE.DAT.

This extracts the actual game's saved profile and earned points for acceptance
evidence. It does not load the game, parse unrelated save payloads or modify files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

from protocol import MAX_CLAIMS, MAX_PERK_CLAIMS, SKILL_NAMES, validate_claims, validate_perk_claims

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
    if not 38 <= len(trailer) <= 40 + MAX_CLAIMS * 21 + MAX_PERK_CLAIMS * 19:
        raise ValueError("invalid training trailer size")
    version, payload = struct.unpack_from("!II", trailer, 8)
    if version not in (4, 5) or payload != len(trailer) - 20:
        raise ValueError("inspection requires a v4 or v5 training trailer")
    checksum, = struct.unpack_from("!I", trailer, len(trailer) - 4)
    if checksum != zlib.crc32(trailer[:-4]):
        raise ValueError("training trailer checksum mismatch")
    count, = struct.unpack_from("!H", trailer, 32)
    skill_end = 34 + count * 21
    if count > MAX_CLAIMS or (version == 4 and len(trailer) != skill_end + 4) \
            or (version == 5 and len(trailer) < skill_end + 6):
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
    perk_claims = {}
    if version == 5:
        perk_count, = struct.unpack_from("!H", trailer, skill_end)
        if not 1 <= perk_count <= MAX_PERK_CLAIMS or len(trailer) != skill_end + 2 + perk_count * 19 + 4:
            raise ValueError("perk claim length/count mismatch")
        for index in range(perk_count):
            start = skill_end + 2 + index * 19
            perk, rank = struct.unpack_from("!HB", trailer, start)
            key = f"{perk}:{rank}"
            if key in perk_claims:
                raise ValueError("duplicate saved perk claim")
            perk_claims[key] = trailer[start+3:start+19].hex()
        validate_perk_claims(perk_claims)
        if set(perk_claims.values()) & {claim["receipt"] for claim in claims.values()}:
            raise ValueError("receipt reused across reward types")
    profile = trailer[16:32].hex()
    if (count or perk_claims) and int(profile, 16) == 0:
        raise ValueError("earned claims require a nonzero profile")
    bonuses = {name: sum(c["points"] for key, c in claims.items() if int(key.split(":")[0]) == skill)
               for skill, name in enumerate(SKILL_NAMES)}
    return {"path": str(path.resolve()), "save_sha256": hashlib.sha256(data).hexdigest(),
            "training_offset": offset, "training_bytes": len(trailer), "version": version,
            "crc32": f"{checksum:08x}", "profile": profile, "claims": claims, "perk_claims": perk_claims,
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
