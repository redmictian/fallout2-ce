"""Explicit operator preparation of one new frozen nginx base, through Docker.

Uses the already qualified common Debian base read-only. Only the disposable
guest downloads/installs packages. Existing bases are never replaced. The final
artifact identity is written to a reviewable manifest when --write-manifest is
explicitly selected; runtime launch never learns or downloads a new identity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import time
from types import SimpleNamespace

from bridge import local_docker_client
from nginx_env import NginxEnvironment, MANIFEST
from nginx_profile import PREPARATIONS

COMMON = "debian-prepared.qcow2"
COMMON_HASH = "5644b6f9ddda404609a7d395a040fc8f57a9e7c712af532c0f5ac314e9f2dd62"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("explicit --run is required")
    args.output.mkdir(parents=True, exist_ok=False)
    spec = SimpleNamespace(kind="qemu-nginx", parameters={"name": ["nginx-preparation"], "preparation": [PREPARATIONS[0]]},
                           title="Operator preparation", instructions="Operator preparation only")
    client = local_docker_client()
    env = NginxEnvironment(client, spec, "nginx-prep-" + secrets.token_hex(5), 0, 0, args.output / "state")
    target = env.manifest["root"]
    env.manifest = {**env.manifest, "root": COMMON, "hashes": {COMMON: COMMON_HASH}}
    result = {"base": COMMON, "base_sha256": COMMON_HASH, "artifact": target}
    try:
        env._launch_runner(network="bridge")
        started = time.monotonic()
        deadline = started + 300
        while time.monotonic() < deadline:
            try:
                ready = env.guest("uname -r; systemctl is-active multi-user.target", timeout=8)
                if ready.get("exitcode") == 0:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise TimeoutError("common guest did not become ready")
        result["boot_seconds"] = time.monotonic() - started
        operation = env.guest("export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends nginx iproute2 curl; systemctl enable nginx; nginx -v; dpkg-query -W nginx nginx-common systemd iproute2 curl; uname -r", timeout=600)
        (args.output / "package-preparation.json").write_text(json.dumps(operation, indent=2) + "\n")
        if operation.get("exitcode") != 0 or operation.get("truncated"):
            raise RuntimeError("guest package preparation failed")
        result["packages"] = operation
        # Filesystem writes complete before the candidate is flattened offline.
        env.guest("sync; systemctl poweroff", timeout=15)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if env._call("status").get("machine", {}).get("status") == "shutdown":
                break
            time.sleep(.5)
        else:
            raise RuntimeError("guest did not shut down cleanly before freezing")
        client.api.stop(env.container_id, timeout=15)
        code = r'''import hashlib,json,os,pathlib,subprocess,sys
target=pathlib.Path('/images')/sys.argv[1]
if target.exists():raise SystemExit('refusing to overwrite an existing artifact')
temporary=target.with_suffix(target.suffix+'.building')
if temporary.exists():raise SystemExit('previous candidate exists; inspect it before retrying')
subprocess.run(['qemu-img','convert','-O','qcow2','/work/root.qcow2',str(temporary)],check=True)
with temporary.open('rb') as source:
 h=hashlib.sha256()
 for block in iter(lambda:source.read(8388608),b''):h.update(block)
os.link(temporary,target); temporary.unlink()
print(json.dumps({'sha256':h.hexdigest(),'bytes':target.stat().st_size}))
'''
        completed = subprocess.run(["docker", "run", "--rm", "--label", "org.nixboy.delivery-artifact=nginx-v1",
            "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--user", "1000:1000", "--read-only",
            "-v", env.volume + ":/work:ro", "-v", env.images_volume + ":/images", env.image,
            "python3", "-I", "-c", code, target], capture_output=True, text=True, timeout=300)
        if completed.returncode:
            raise RuntimeError(completed.stderr or completed.stdout)
        result.update(json.loads(completed.stdout))
        if args.write_manifest:
            manifest = json.loads(MANIFEST.read_text())
            manifest["hashes"] = {target: result["sha256"]}
            manifest["packages"] = operation["stdout"].splitlines()
            MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        result["elapsed_seconds"] = time.monotonic() - started
        result["passed"] = True
        print(json.dumps(result, indent=2))
    finally:
        env.close()
        (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
