"""Export/validate a private atomic training snapshot, including educational progress.

Recovery is deliberately offline: stop the owning provider, validate a backup,
preserve existing state, then restore it. Version 6 game-save references also need
the companion progression-snapshots directory; export copies that immutable cache.
No credentials are included here.
"""
import argparse
import json
import os
from pathlib import Path
import re
import hashlib
from protocol import validate_claims, validate_perk_claims


def validate_profile(identity, profile):
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
    if {record["receipt"] for record in records.values()} & {record["receipt"] for record in profile.get("receipts", {}).values()}:
        raise ValueError("receipt reused across reward kinds")
    if not isinstance(profile.get("queues", {}), dict) or profile.get("attempt") is not None and not isinstance(profile["attempt"], dict):
        raise ValueError("invalid training queue/attempt")
    education = profile.get("education")
    if education is not None and (not isinstance(education, dict) or education.get("version") != 1 or
            any(not isinstance(education.get(key), dict) for key in ("completions", "queues", "seen")) or
            not isinstance(education.get("history"), list)):
        raise ValueError("unsupported educational record version")


def validate_activations(activations):
    if not isinstance(activations, dict):
        raise ValueError("invalid progression activations")
    for identity, metadata in activations.items():
        if not re.fullmatch(r"[0-9a-f]{32}", identity) or not isinstance(metadata, dict):
            raise ValueError("invalid progression activation profile")
        epoch, retired = metadata.get("activation"), metadata.get("retired", [])
        if not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f]{32}", epoch) or epoch == "0" * 32 \
                or not isinstance(retired, list) or len(retired) > 4096 or len(set(retired)) != len(retired) \
                or epoch in retired or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) or value == "0" * 32 for value in retired):
            raise ValueError("invalid progression activation history")
        snapshot = metadata.get("restored_from")
        if not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
            raise ValueError("invalid progression snapshot reference")


def read_snapshot(path):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("training snapshot exceeds 8 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") not in (4, 5) or not isinstance(data.get("profiles"), dict):
        raise ValueError("expected bridge snapshot version 4 or 5")
    for identity, profile in data["profiles"].items():
        validate_profile(identity, profile)
    if data["version"] == 5:
        validate_activations(data.get("activations", {}))
    if "backup_progression_snapshots" in data:
        index = data["backup_progression_snapshots"]
        if not isinstance(index, list) or len(index) != len(set(index)):
            raise ValueError("invalid progression backup index")
        companion = Path(str(path) + ".progression-snapshots")
        for digest in index:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid progression backup reference")
            content = (companion / (digest + ".json")).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("corrupt progression backup companion")
    return data


def export_snapshot(state_dir, output):
    source = Path(state_dir).resolve() / "training.json"
    destination = Path(output).resolve()
    if destination == source:
        raise ValueError("backup must be separate from active training.json")
    data = read_snapshot(source)  # Writer replaces this single file atomically.
    snapshots = Path(state_dir).resolve() / "progression-snapshots"
    companion = Path(str(destination) + ".progression-snapshots")
    copied = 0
    index = []
    if data["version"] == 5 and snapshots.exists():
        companion.mkdir(mode=0o700)  # Deliberately nonoverwriting.
        for path in sorted(snapshots.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name) or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("invalid immutable progression snapshot")
            value = path.read_bytes()
            if hashlib.sha256(value).hexdigest() != path.stem:
                raise ValueError("corrupt immutable progression snapshot; preserve it for recovery")
            with (companion / path.name).open("xb") as stream:
                if os.name != "nt":
                    os.chmod(companion / path.name, 0o600)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            copied += 1
            index.append(path.stem)
    if data["version"] == 5:
        data["backup_progression_snapshots"] = index
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return {"profiles": len(data["profiles"]), "output": str(destination),
            "progression_snapshots": copied, "companion": str(companion) if copied else None}


def restore_snapshot(backup, state_dir):
    """Restore a complete validated bundle into a NEW offline state directory."""
    backup, destination = Path(backup).resolve(), Path(state_dir).resolve()
    data = read_snapshot(backup)
    index = data.pop("backup_progression_snapshots", [])
    destination.mkdir(mode=0o700)  # Existing player/test state is never overwritten.
    if index:
        directory = destination / "progression-snapshots"
        directory.mkdir(mode=0o700)
        for digest in index:
            content = (Path(str(backup)+".progression-snapshots")/(digest+".json")).read_bytes()
            descriptor = os.open(directory/(digest+".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
    descriptor = os.open(destination/"training.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    return {"profiles": len(data["profiles"]), "state_dir": str(destination), "progression_snapshots": len(index)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("--state-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("backup", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "export":
        result = export_snapshot(args.state_dir, args.output)
    elif args.action == "restore":
        result = restore_snapshot(args.backup, args.state_dir)
    else:
        result = {"profiles": len(read_snapshot(args.backup)["profiles"]), "valid": True}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
