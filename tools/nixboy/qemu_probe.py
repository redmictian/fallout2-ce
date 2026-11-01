"""Read-only BT05 guest observations, sent transiently by the host validator.

There is deliberately no guest-owned success file or persistent validator module.
Expected values and pass decisions remain in qemu_env.py outside the guest.
"""
import hashlib
import json
from pathlib import Path
import subprocess


def command(*argv):
    value = subprocess.run(argv, text=True, capture_output=True, timeout=8)
    return {"rc": value.returncode, "stdout": value.stdout.strip(), "stderr": value.stderr[-1000:]}


def file(path):
    try:
        p = Path(path)
        s = p.stat()
        return {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": s.st_size,
                "uid": s.st_uid, "gid": s.st_gid, "mode": s.st_mode & 0o7777,
                "mtime": int(s.st_mtime)}
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}


def main():
    service = command("/usr/bin/systemctl", "show", "nbq-http.service", "--property=ActiveState,UnitFileState,User,MainPID,FragmentPath")
    properties = dict(line.split("=", 1) for line in service["stdout"].splitlines() if "=" in line)
    pid = properties.get("MainPID", "0")
    worker = {}
    if pid.isdigit() and int(pid) > 0:
        try:
            worker = {"status": Path("/proc/" + pid + "/status").read_text(),
                      "cmdline": Path("/proc/" + pid + "/cmdline").read_bytes().replace(b"\0", b" ").decode()}
        except OSError as exc:
            worker = {"error": str(exc)}
    result = {"boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "kernel": command("/usr/bin/uname", "-r"),
              "normal": command("/usr/bin/systemctl", "is-active", "multi-user.target"),
              "emergency": command("/usr/bin/systemctl", "is-active", "emergency.target"),
              "mount": command("/usr/bin/findmnt", "-J", "-M", "/srv/recovery-data", "-o", "TARGET,SOURCE,UUID,FSTYPE,OPTIONS"),
              "fstab": Path("/etc/fstab").read_text(), "service": properties, "worker": worker,
              "payload": file("/srv/recovery-data/sentinel.txt"),
              "root_peer": file("/root/nbq-protected.txt"),
              "unit": file("/etc/systemd/system/nbq-http.service")}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
