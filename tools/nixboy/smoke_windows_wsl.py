#!/usr/bin/env python3
"""Opt-in native Windows C++ client -> WSL bridge -> real Docker smoke.

Build tests/nixboy_live_probe.cc with src/nixboy_client.cc for Windows first.
The ephemeral authentication token travels only through anonymous stdin pipes.
No game, assets, token files, Docker installation or daemon changes are needed.
"""

import argparse
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time


def serve_wsl(args):
    if os.name != "posix":
        raise RuntimeError("the bridge helper must run inside WSL")
    import docker
    from bridge import BridgeServer, local_docker_client
    from protocol import TOKEN

    line = sys.stdin.buffer.readline(66)
    token = line[:-1]
    if not line.endswith(b"\n") or not TOKEN.fullmatch(token):
        raise RuntimeError("expected a private 64-byte token line on stdin")
    owned_ids = set()
    owned_lock = threading.Lock()

    def factory():
        client = local_docker_client()
        create = client.api.create_container

        def tracked_create(*positional, **keywords):
            created = create(*positional, **keywords)
            with owned_lock:
                owned_ids.add(created["Id"])
            return created

        client.api.create_container = tracked_create
        return client

    inspector = local_docker_client()
    try:
        inspector.ping()
        if inspector.info().get("OSType") != "linux":
            raise RuntimeError("the daemon must run Linux containers")
        inspector.images.get(args.image)  # Never pull implicitly.
        server = BridgeServer(0, token, args.image, client_factory=factory)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        try:
            print("READY " + str(server.server_address[1]), flush=True)
            if sys.stdin.buffer.read(1):
                raise RuntimeError("unexpected bridge supervisor input")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        if thread.is_alive() or not server.handlers_done.is_set():
            raise RuntimeError("bridge cleanup did not complete")
        with owned_lock:
            created_ids = list(owned_ids)
        for container_id in created_ids:
            try:
                inspector.containers.get(container_id)
            except docker.errors.NotFound:
                continue
            raise RuntimeError("probe container was not removed: " + container_id)
        if not created_ids:
            raise RuntimeError("probe never created a Docker container")
    finally:
        inspector.close()


def run_windows(args):
    if os.name != "nt":
        raise RuntimeError("run this supervisor with native Windows Python")
    if not args.probe:
        raise RuntimeError("--probe must name the compiled Windows probe executable")
    probe_path = Path(args.probe).resolve(strict=True)
    script_path = subprocess.run(
        ["wsl", "-d", args.distribution, "--exec", "wslpath", "-a",
         Path(__file__).resolve().as_posix()],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    if not script_path.startswith("/") or "\n" in script_path:
        raise RuntimeError("WSL did not return an absolute script path")
    token = secrets.token_hex(32).encode("ascii")
    service = subprocess.Popen(
        ["wsl", "-d", args.distribution, "--exec", args.wsl_python, script_path,
         "--serve-wsl", "--run", "--image", args.image],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    ready = queue.Queue(maxsize=1)
    diagnostic = bytearray()

    def read_ready():
        ready.put(service.stdout.readline(128))

    def drain_errors():
        while True:
            chunk = service.stderr.read(1024)
            if not chunk:
                return
            if len(diagnostic) < 32768:
                diagnostic.extend(chunk[:32768 - len(diagnostic)])

    reader = threading.Thread(target=read_ready, daemon=True)
    errors = threading.Thread(target=drain_errors, daemon=True)
    reader.start()
    errors.start()
    result = None
    failure = None
    try:
        service.stdin.write(token + b"\n")
        service.stdin.flush()
        try:
            announcement = ready.get(timeout=30).decode("ascii").strip()
        except queue.Empty as exc:
            raise RuntimeError("WSL bridge startup exceeded 30 seconds") from exc
        if not announcement.startswith("READY ") or not announcement[6:].isdigit():
            raise RuntimeError("WSL bridge failed to announce its loopback port")
        port = int(announcement[6:])
        if not 1 <= port <= 65535:
            raise RuntimeError("WSL bridge announced an invalid port")
        # WSL's Windows localhost listener can appear after bind() returns in
        # Linux. Wait for forwarding before exercising Client's 250 ms connect.
        forwarding_deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    break
            except OSError as exc:
                if time.monotonic() >= forwarding_deadline:
                    raise RuntimeError("Windows-to-WSL localhost forwarding is unavailable: " + str(exc)) from exc
                time.sleep(0.1)
        result = subprocess.run(
            [str(probe_path), str(port)], input=token + b"\n",
            capture_output=True, timeout=25,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()
                               or "native C++ probe failed")
    except BaseException as exc:
        failure = exc
    finally:
        token = b""
        # EOF requests orderly cleanup, including active sessions. An already
        # failed helper can close its end before the supervisor reaches here.
        try:
            service.stdin.close()
        except OSError:
            pass
        try:
            service.wait(timeout=45)
        except subprocess.TimeoutExpired:
            service.terminate()
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)
            failure = failure or RuntimeError("WSL bridge cleanup exceeded 45 seconds")
        reader.join(2)
        errors.join(2)
        service.stdout.close()
        service.stderr.close()
    if service.returncode:
        details = diagnostic.decode("utf-8", "replace").strip()
        failure = failure or RuntimeError(details or "WSL bridge cleanup failed")
    if failure:
        if diagnostic:
            print(diagnostic.decode("utf-8", "replace").strip(), file=sys.stderr)
        raise failure
    print(result.stdout.decode("utf-8", "replace").strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="create ephemeral Docker test containers")
    parser.add_argument("--probe", help="path to the compiled native Windows live probe")
    parser.add_argument("--distribution", default="Ubuntu-26.04")
    parser.add_argument("--wsl-python", default="python3", help="WSL Python with requirements.txt installed")
    parser.add_argument("--image", default="fallout2-nixboy:dev")
    parser.add_argument("--serve-wsl", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to execute the real-Docker smoke test")
    try:
        serve_wsl(args) if args.serve_wsl else run_windows(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print("Windows/WSL smoke failed: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
