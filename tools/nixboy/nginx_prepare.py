"""Guest preparation invoked by the trusted operator, before learner access."""
import json
import os
from pathlib import Path
import subprocess
import sys


def run(*argv, check=True):
    return subprocess.run(argv, check=check, capture_output=True, text=True)


def main():
    data = json.loads(sys.argv[1])
    # Data/configuration are supplied by a fixed trusted profile, never YAML code.
    for name in ("nb-client-allowed", "nb-client-denied"):
        run("ip", "netns", "del", name, check=False)
    run("ip", "link", "del", "nb-br", check=False)
    run("ip", "link", "add", "nb-br", "type", "bridge")
    run("ip", "addr", "add", "10.77.0.1/24", "dev", "nb-br")
    run("ip", "link", "set", "nb-br", "up")
    for suffix, address in (("allowed", "10.77.0.2"), ("denied", "10.77.0.3")):
        namespace = "nb-client-" + suffix
        outer, inner = "nb-" + suffix, "eth0"
        run("ip", "netns", "add", namespace)
        run("ip", "link", "add", outer, "type", "veth", "peer", "name", "nb-peer")
        run("ip", "link", "set", "nb-peer", "netns", namespace)
        run("ip", "-n", namespace, "link", "set", "nb-peer", "name", inner)
        run("ip", "-n", namespace, "addr", "add", address + "/24", "dev", inner)
        run("ip", "-n", namespace, "link", "set", inner, "up")
        run("ip", "-n", namespace, "link", "set", "lo", "up")
        run("ip", "link", "set", outer, "master", "nb-br")
        run("ip", "link", "set", outer, "up")
    # Recreate only this fixed topology on a genuine guest reboot. This is
    # fixture setup, not a validator or learner-success interface.
    startup = '''#!/bin/sh
set -eu
ip netns del nb-client-allowed 2>/dev/null || true
ip netns del nb-client-denied 2>/dev/null || true
ip link del nb-br 2>/dev/null || true
ip link add nb-br type bridge
ip addr add 10.77.0.1/24 dev nb-br
ip link set nb-br up
for pair in allowed:10.77.0.2 denied:10.77.0.3; do
 role=${pair%%:*}; address=${pair#*:}; ns=nb-client-$role
 ip netns add "$ns"
 ip link add nb-$role type veth peer name nb-peer
 ip link set nb-peer netns "$ns"
 ip -n "$ns" link set nb-peer name eth0
 ip -n "$ns" addr add "$address/24" dev eth0
 ip -n "$ns" link set eth0 up
 ip -n "$ns" link set lo up
 ip link set nb-$role master nb-br
 ip link set nb-$role up
done
'''
    script = Path("/usr/local/sbin/nixboy-clients")
    script.write_text(startup)
    script.chmod(0o755)
    Path("/etc/systemd/system/nixboy-clients.service").write_text('''[Unit]
Description=Fixed nix-boy independent client fixture
Before=nginx.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/nixboy-clients
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
    override = Path("/etc/systemd/system/nginx.service.d")
    override.mkdir(exist_ok=True)
    (override / "nixboy-clients.conf").write_text("[Unit]\nRequires=nixboy-clients.service\nAfter=nixboy-clients.service\n")
    target = Path("/srv/nixboy-nginx")
    target.mkdir(parents=True, exist_ok=True)
    for name, text in data["files"].items():
        path = target / name
        path.write_text(text)
        path.chmod(0o644)
        os.chown(path, 0, 0)
        os.utime(path, (1768478400, 1768478400))
    Path("/etc/nginx/nginx.conf").write_text(data["configuration"])
    run("/usr/sbin/nginx", "-t")
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "nixboy-clients")
    run("systemctl", "enable", "nginx")
    run("systemctl", "restart", "nginx")
    run("journalctl", "--sync")
    print(json.dumps({"prepared": data["preparation"], "native_manager": True}))


if __name__ == "__main__":
    main()
