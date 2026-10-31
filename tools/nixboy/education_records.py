"""Export/validate a private atomic training snapshot, including educational progress.

Recovery is deliberately offline: stop the owning provider, validate a backup,
preserve the existing training.json, then restore the complete snapshot. Game
saves do not contain educational records. No credentials are included here.
"""
import argparse
import json
import os
from pathlib import Path
from protocol import validate_claims, validate_perk_claims


def read_snapshot(path):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("training snapshot exceeds 8 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 4 or not isinstance(data.get("profiles"), dict):
        raise ValueError("expected bridge snapshot version 4")
    for identity, profile in data["profiles"].items():
        if not isinstance(profile, dict) or not isinstance(identity, str) or len(identity) != 32 \
                or any(ch not in "0123456789abcdef" for ch in identity):
            raise ValueError("invalid training profile")
        validate_claims(profile.get("receipts", {}))
        records = profile.get("perk_receipts", {})
        if not isinstance(records, dict) or any(not isinstance(record, dict) for record in records.values()):
            raise ValueError("invalid typed perk receipt records")
        validate_perk_claims({key: record.get("receipt") for key, record in records.items()})
        if any(record.get("profile") != identity for record in records.values()):
            raise ValueError("typed perk receipt profile mismatch")
        education = profile.get("education")
        if education is not None and (education.get("version") != 1 or
                any(not isinstance(education.get(key), dict) for key in ("completions", "queues", "seen")) or
                not isinstance(education.get("history"), list)):
            raise ValueError("unsupported educational record version")
    return data


def export_snapshot(state_dir, output):
    source = Path(state_dir).resolve() / "training.json"
    destination = Path(output).resolve()
    if destination == source:
        raise ValueError("backup must be separate from active training.json")
    data = read_snapshot(source)  # Writer replaces this single file atomically.
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return {"profiles": len(data["profiles"]), "output": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("--state-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("backup", type=Path)
    args = parser.parse_args()
    result = (export_snapshot(args.state_dir, args.output) if args.action == "export" else
              {"profiles": len(read_snapshot(args.backup)["profiles"]), "valid": True})
    print(json.dumps(result))


if __name__ == "__main__":
    main()
