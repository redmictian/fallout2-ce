"""Read-only guest observations; host code owns expected values and decisions."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess


def command(argv, timeout=6):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {"code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr[-500:]}


def main():
    service = command(["systemctl", "show", "nginx", "-p", "ActiveState", "-p", "UnitFileState", "-p", "MainPID", "-p", "ExecReload", "-p", "InvocationID"])
    properties = dict(line.split("=", 1) for line in service["stdout"].splitlines() if "=" in line)
    pid = int(properties.get("MainPID", "0"))
    workers = []
    if pid > 1:
        try:
            for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split():
                status = Path(f"/proc/{child}/status").read_text()
                fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
                workers.append({"pid": int(child), "uid": fields.get("Uid", "").split(),
                                "gid": fields.get("Gid", "").split(),
                                "cmd": Path(f"/proc/{child}/cmdline").read_bytes().replace(b"\0", b" ").decode()})
        except FileNotFoundError:
            pass  # A worker transition is not a stable sampled credential view.
    nonce = "nbx-" + secrets.token_hex(8)
    requests = {}
    for role, namespace in (("allowed", "nb-client-allowed"), ("denied", "nb-client-denied")):
        requests[role] = {}
        for path in ("/public.txt", "/staff.txt", "/stage2"):
            marker = nonce + "-" + role + path.replace("/", "-")
            result = subprocess.run(["ip", "netns", "exec", namespace, "curl", "--noproxy", "*", "--silent", "--show-error", "--max-time", "3", "-D", "-",
                "-H", "Host: lesson.nixboy.invalid", "-H", "X-Nixboy-Probe: " + marker, "http://10.77.0.1:8080" + path], capture_output=True, timeout=5)
            raw = result.stdout
            header, separator, body = raw.partition(b"\r\n\r\n")
            lines = header.decode("latin1").splitlines()
            headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            requests[role][path] = {"code": result.returncode,
                "status": int(lines[0].split()[1]) if lines and len(lines[0].split()) >= 2 else None,
                "body": body.decode("utf-8", "replace"), "sha256": hashlib.sha256(body).hexdigest(),
                "host_id": next((v.strip() for k, v in headers.items() if k.lower() == "x-nixboy-host"), None),
                "marker": marker, "error": result.stderr.decode("utf-8", "replace")[-300:]}
    logs = []
    path = Path("/var/log/nginx/nixboy-access.jsonl")
    if path.exists():
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            for line in stream.read().splitlines():
                try:
                    value = json.loads(line)
                    if value.get("probe", "").startswith(nonce):
                        logs.append(value)
                except ValueError:
                    pass
    files = {}
    for name in ("public.txt", "staff.txt", "stage1.reference"):
        path = Path("/srv/nixboy-nginx") / name
        try:
            info = path.stat()
            files[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "uid": info.st_uid,
                           "gid": info.st_gid, "mode": info.st_mode & 0o777, "mtime": int(info.st_mtime)}
        except OSError:
            files[name] = {}
    print(json.dumps({"boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "pid1": Path("/proc/1/comm").read_text().strip(), "normal": command(["systemctl", "is-active", "multi-user.target"]),
        "service": properties, "workers": workers, "requests": requests, "logs": logs, "files": files,
        "nginx_test": command(["nginx", "-t"]), "nginx_configuration": command(["nginx", "-T"]),
        "journal": command(["journalctl", "-u", "nginx", "-b", "-n", "20", "--no-pager", "-o", "cat"]),
        "journal_socket": Path("/run/systemd/journal/socket").exists()}))


if __name__ == "__main__":
    main()
