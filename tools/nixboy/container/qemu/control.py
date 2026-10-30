"""CLI used via Docker exec; no host-side VM API."""
import argparse
import json
import os
import select
import socket
import sys
import termios
import tty

p = argparse.ArgumentParser()
p.add_argument("method", choices=["guest", "qmp", "serial-read", "serial-send", "status", "console"])
p.add_argument("value", nargs="?")
p.add_argument("--timeout", type=int, default=300)
a = p.parse_args()
if a.method == "console":
    previous = termios.tcgetattr(sys.stdin.fileno()) if sys.stdin.isatty() else None
    if previous is not None:
        tty.setraw(sys.stdin.fileno())
    s = socket.socket(socket.AF_UNIX)
    s.connect("/tmp/nbq-console.sock")
    try:
        while True:
            ready, _, _ = select.select([s, sys.stdin.buffer], [], [])
            if s in ready:
                data = s.recv(16384)
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            if sys.stdin.buffer in ready:
                data = os.read(sys.stdin.fileno(), 16384)
                if not data:
                    break
                s.sendall(data)
    finally:
        s.close()
        if previous is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, previous)
else:
    request = {"method": a.method}
    if a.method == "guest":
        request.update(script=a.value if a.value is not None else sys.stdin.read(), timeout=a.timeout)
    elif a.method == "qmp":
        command = json.loads(a.value if a.value else sys.stdin.read())
        request.update(command=command["execute"], arguments=command.get("arguments"))
    elif a.method == "serial-send":
        request["text"] = a.value if a.value is not None else sys.stdin.read()
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(a.timeout + 10)
    s.connect("/work/api.sock")
    s.sendall((json.dumps(request) + "\n").encode())
    result = json.loads(s.makefile("rb").readline())
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)
