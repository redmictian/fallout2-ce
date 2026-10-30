"""Verify/import the exact operator-supplied BT05 disks; never trust new hashes.

This utility does not boot a guest or touch game state. Rebuilding a canonical
fixture is a deliberate authoring/qualification operation, followed by review of
the checked-in manifest; it is not automatic trust-on-first-use.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from qemu_env import manifest, settings

LABEL = "org.fallout2-ce.nixboy.qemu-artifacts=bt05-a-v1"


def docker(*args):
    # Windows target runs this tool inside the existing WSL Docker provider.
    result = subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", *args],
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])
    return result.stdout


def verify(volume=None):
    image, configured = settings()
    volume = volume or configured
    docker("volume", "inspect", volume)  # A missing volume is not silently created.
    expected = manifest()["hashes"]
    code = """import hashlib,json,pathlib,sys
expected=json.loads(sys.argv[1]); actual={}
for name,want in expected.items():
 p=pathlib.Path('/images')/name; h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8388608),b''): h.update(b)
 actual[name]=h.hexdigest()
 if actual[name]!=want: raise SystemExit('canonical hash mismatch: '+name)
print(json.dumps({'verified':True,'sha256':actual}))
"""
    return json.loads(docker("run", "--rm", "--label", LABEL, "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", "1000:1000", "--read-only",
        "-v", volume + ":/images:ro", image, "python3", "-I", "-c", code, json.dumps(expected)))


def import_directory(directory):
    directory = Path(directory).resolve(strict=True)
    expected = manifest()["hashes"]
    for name, want in expected.items():
        source = directory / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("missing regular artifact: " + name)
        h = hashlib.sha256()
        with source.open("rb") as f:
            for b in iter(lambda: f.read(8388608), b""):
                h.update(b)
        if h.hexdigest() != want:
            raise ValueError("source artifact hash mismatch: " + name)
    image, volume = settings()
    docker("volume", "create", "--label", LABEL, volume)
    code = """import pathlib,sys,json,hashlib,shutil
for name,want in json.loads(sys.argv[1]).items():
 p=pathlib.Path('/images')/name
 if p.exists():
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(8388608),b''):h.update(b)
  if h.hexdigest()!=want: raise SystemExit('refusing existing different artifact: '+name)
 else:
  with (pathlib.Path('/input')/name).open('rb') as src,p.open('xb') as dst:shutil.copyfileobj(src,dst,8388608)
"""
    docker("run", "--rm", "--label", LABEL, "--network", "none", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges:true", "--user", "1000:1000", "--read-only",
           "-v", str(directory) + ":/input:ro", "-v", volume + ":/images", image,
           "python3", "-I", "-c", code, json.dumps(expected))
    return verify()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "import"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.operation == "import" and args.directory is None:
        parser.error("import requires --directory with the three manifest-matching disks")
    print(json.dumps(import_directory(args.directory) if args.operation == "import" else verify(), indent=2))


if __name__ == "__main__":
    main()
