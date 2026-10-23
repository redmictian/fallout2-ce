"""Test-only reference solutions. Never copied into learner images."""

import shlex


def solve(environment):
    name = shlex.quote(environment.params["name"])
    kind = environment.spec.kind
    commands = {
        "make-directory": f"mkdir -p ~/lab/{name}/ready",
        "copy-file": f"cp ~/lab/incoming/{name}.txt ~/lab/delivery/manifest.txt",
        "move-file": f"mv ~/lab/incoming/{name}.txt ~/lab/archive/",
        "rename-directory": f"mv ~/lab/{name}-draft ~/lab/{name}-final",
        "remove-empty": f"rmdir ~/lab/{name}",
        "private-file": f"chmod u=rw,go= ~/lab/{name}.txt",
        "public-read": f"chmod u=rw,go=r ~/lab/{name}.txt",
        "executable": f"chmod u=rwx,go= ~/lab/{name}.sh && ~/lab/{name}.sh",
        "shared-directory": f"chmod 0770 ~/lab/{name}",
        "group-readable": f"chmod u=rw,g=r,o= ~/lab/{name}.txt",
        "filter-lines": f"grep '^ERROR ' ~/lab/{name}.log > ~/lab/result.txt",
        "sort-unique": f"LC_ALL=C sort -u ~/lab/{name}.txt > ~/lab/result.txt",
        "extract-field": f"cut -d: -f2 ~/lab/{name}.txt > ~/lab/result.txt",
        "count-matches": f"grep -c '^WARN ' ~/lab/{name}.log > ~/lab/result.txt",
        "replace-text": f"sed 's/offline/online/g' ~/lab/{name}.conf > ~/lab/result.txt",
        "ssh-upload": f"scp -o BatchMode=yes ~/lab/{name}.txt nixboy@server:/home/nixboy/delivery.txt",
        "ssh-download": f"scp -o BatchMode=yes nixboy@server:/home/nixboy/{name}.txt ~/lab/result.txt",
        "ssh-remote-move": f"ssh -o BatchMode=yes nixboy@server 'mv /home/nixboy/incoming/{name}.txt /home/nixboy/archive/'",
    }
    if kind == "ssh-key-login":
        environment._write("workstation", "/home/nixboy/.test-askpass", "#!/bin/sh\nprintf 'training\\n'\n", 0o700)
        code, output, error = environment._exec("workstation", ["/usr/bin/env", "DISPLAY=:0",
            "SSH_ASKPASS=/home/nixboy/.test-askpass", "SSH_ASKPASS_REQUIRE=force", "/usr/bin/ssh-copy-id",
            "-i", "/home/nixboy/.ssh/id_ed25519.pub", "nixboy@server"])
        environment._exec("workstation", ["/bin/rm", "-f", "/home/nixboy/.test-askpass"])
    elif kind == "ssh-alias":
        environment._write("workstation", "/home/nixboy/.ssh/config",
                           f"Host {name}\n  HostName server\n  User nixboy\n  IdentityFile ~/.ssh/id_ed25519\n", 0o600)
        code, output, error = 0, "", ""
    else:
        code, output, error = environment._exec("workstation", ["/bin/bash", "--noprofile", "--norc", "-c", commands[kind]])
    if code:
        raise AssertionError(f"reference solution failed ({kind}): {output} {error}")


def wrong(environment):
    """A plausible incorrect state, not a magic success command or validator emit."""
    kind, name = environment.spec.kind, environment.params["name"]
    if kind in ("private-file", "public-read", "group-readable", "executable"):
        extension = "sh" if kind == "executable" else "txt"
        environment._exec("workstation", ["/bin/chmod", "777", f"/home/nixboy/lab/{name}.{extension}"])
    elif kind == "shared-directory":
        environment._exec("workstation", ["/bin/chmod", "777", f"/home/nixboy/lab/{name}"])
    elif kind == "copy-file":
        environment._write("workstation", "/home/nixboy/lab/delivery/manifest.txt", "wrong\n")
    elif kind == "ssh-upload":
        environment._write("server", "/home/nixboy/delivery.txt", "wrong\n")
    elif kind == "ssh-alias":
        environment._write("workstation", "/home/nixboy/.ssh/config", f"Host {name}\n  HostName missing.invalid\n")
    else:
        environment._write("workstation", "/home/nixboy/lab/result.txt", "wrong\n")
