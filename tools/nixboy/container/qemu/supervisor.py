"""Docker-managed guest runner. QEMU is independent of console exec clients."""
import base64
import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import socketserver
import subprocess
import threading
import time
from serial_history import replay_bytes

WORK = Path("/work")
EVIDENCE = Path("/evidence")
CONFIG = json.loads(os.environ["VM_CONFIG"])
EVIDENCE.mkdir(exist_ok=True)
SERIAL = bytearray()
SERIAL_LOCK = threading.Lock()
SERIAL_SEND = threading.Lock()
SERIAL_SOCK = None
QGA_LOCK = threading.Lock()
CLIENTS = set()
STOP = threading.Event()


def record(name, item):
    with (EVIDENCE / name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(time=time.time(), **item)) + "\n")


def connect(path, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = socket.socket(socket.AF_UNIX)
        try:
            s.connect(str(path))
            return s
        except (FileNotFoundError, ConnectionRefusedError):
            s.close()
            time.sleep(.2)
    raise TimeoutError(str(path))


class QMP:
    def __init__(self):
        self.s = connect(WORK / "qmp.sock")
        self.reader = self.s.makefile("rb")
        self.greeting = json.loads(self.reader.readline())
        self.cv = threading.Condition()
        self.responses = {}
        self.sequence = 0
        self.write_lock = threading.Lock()
        threading.Thread(target=self.read, daemon=True).start()
        self.call("qmp_capabilities")

    def read(self):
        for line in self.reader:
            try:
                value = json.loads(line)
                if "event" in value:
                    record("qmp-events.jsonl", value)
                elif "id" in value:
                    with self.cv:
                        self.responses[value["id"]] = value
                        self.cv.notify_all()
            except Exception as e:
                record("controller.jsonl", {"qmp_read_error": str(e)})

    def call(self, command, arguments=None, timeout=30):
        with self.write_lock:
            self.sequence += 1
            ident = self.sequence
            item = {"execute": command, "id": ident}
            if arguments is not None:
                item["arguments"] = arguments
            self.s.sendall((json.dumps(item) + "\n").encode())
        end = time.monotonic() + timeout
        with self.cv:
            while ident not in self.responses:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(command)
                self.cv.wait(remaining)
            result = self.responses.pop(ident)
        if "error" in result:
            raise RuntimeError(json.dumps(result["error"]))
        return result.get("return")


def qga_call(s, reader, command, args=None):
    item = {"execute": command}
    if args is not None:
        item["arguments"] = args
    s.sendall((json.dumps(item) + "\n").encode())
    while True:
        line = reader.readline()
        if not line:
            raise RuntimeError("guest agent disconnected")
        line = line.rsplit(b"\xff", 1)[-1]
        try:
            response = json.loads(line)
        except ValueError:
            continue
        if "error" in response:
            raise RuntimeError(json.dumps(response["error"]))
        if "return" in response:
            return response["return"]


def qga_sync(s, reader):
    ident = time.time_ns() % (2**52)
    request = {"execute": "guest-sync-delimited", "arguments": {"id": ident}}
    s.sendall(b"\xff" + (json.dumps(request) + "\n").encode())
    while True:
        line = reader.readline()
        if not line:
            raise RuntimeError("guest agent disconnected during sync")
        try:
            response = json.loads(line.rsplit(b"\xff", 1)[-1])
        except ValueError:
            continue
        if response.get("return") == ident:
            return


def guest_run(script, timeout):
    with QGA_LOCK:
        s = connect(WORK / "qga.sock", timeout=min(timeout, 30))
        s.settimeout(timeout)
        try:
            with s.makefile("rb") as reader:
                qga_sync(s, reader)
                qga_call(s, reader, "guest-ping")
                child = qga_call(s, reader, "guest-exec", {
                    "path": "/usr/bin/timeout",
                    "arg": ["--signal=TERM", "--kill-after=1", str(max(1, timeout - 2)),
                            "/bin/bash", "--noprofile", "--norc", "-c", script],
                    "capture-output": True,
                })["pid"]
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    status = qga_call(s, reader, "guest-exec-status", {"pid": child})
                    if status.get("exited"):
                        result = {"exitcode": status.get("exitcode"), "signal": status.get("signal")}
                        for source, target in (("out-data", "stdout"), ("err-data", "stderr")):
                            result[target] = base64.b64decode(status.get(source, "")).decode("utf-8", "replace")
                        result["truncated"] = status.get("out-truncated", False) or status.get("err-truncated", False)
                        return result
                    time.sleep(.25)
                raise TimeoutError("guest execution still running; guest PID " + str(child))
        finally:
            s.close()


def serial_worker():
    global SERIAL_SOCK
    SERIAL_SOCK = connect(WORK / "serial.sock")
    with (EVIDENCE / "serial.log").open("ab", buffering=0) as f:
        while not STOP.is_set():
            data = SERIAL_SOCK.recv(65536)
            if not data:
                break
            f.write(data)
            with SERIAL_LOCK:
                SERIAL.extend(data)
                if len(SERIAL) > 262144:
                    del SERIAL[:-262144]
                clients = list(CLIENTS)
            for client in clients:
                try:
                    client.sendall(data)
                except OSError:
                    with SERIAL_LOCK:
                        CLIENTS.discard(client)


class ConsoleHandler(socketserver.BaseRequestHandler):
    def handle(self):
        with SERIAL_LOCK:
            self.request.sendall(replay_bytes(bytes(SERIAL)))
            CLIENTS.add(self.request)
        try:
            while True:
                data = self.request.recv(16384)
                if not data:
                    break
                with SERIAL_SEND:
                    SERIAL_SOCK.sendall(data)
        finally:
            with SERIAL_LOCK:
                CLIENTS.discard(self.request)


class APIHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline(1048576))
        started = time.monotonic()
        try:
            kind = request["method"]
            if kind == "qmp":
                result = QMP_CLIENT.call(request["command"], request.get("arguments"))
            elif kind == "guest":
                result = guest_run(request["script"], request.get("timeout", 300))
            elif kind == "serial-read":
                with SERIAL_LOCK:
                    result = bytes(SERIAL).decode("utf-8", "replace")
            elif kind == "serial-send":
                with SERIAL_SEND:
                    SERIAL_SOCK.sendall(request["text"].encode())
                result = True
            elif kind == "status":
                result = {"qemu_pid": VM.pid, "qemu_exit": VM.poll(), "machine": QMP_CLIENT.call("query-status")}
            else:
                raise ValueError("unsupported operation")
            response = {"ok": True, "result": result, "elapsed": time.monotonic() - started}
        except Exception as e:
            response = {"ok": False, "error": str(e), "elapsed": time.monotonic() - started}
        self.wfile.write((json.dumps(response) + "\n").encode())


class ThreadServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def image(path, base=None, size=None):
    if path.exists():
        return
    argv = ["qemu-img", "create", "-f", "qcow2"]
    if base:
        argv += ["-F", "qcow2", "-b", base]
    argv += [str(path)]
    if size:
        argv += [size]
    subprocess.run(argv, check=True)


def start():
    # A base is never accepted solely because a mutable image/volume name matches.
    for name, expected in CONFIG.get("hashes", {}).items():
        if Path(name).name != name or not name.endswith(".qcow2"):
            raise ValueError("invalid canonical base filename")
        digest = hashlib.sha256()
        with (Path("/images") / name).open("rb") as source:
            for data in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(data)
        if digest.hexdigest() != expected:
            raise RuntimeError("canonical base hash mismatch: " + name)
    record("controller.jsonl", {"bases_verified": True})
    for endpoint in ("qmp.sock", "qga.sock", "serial.sock"):
        (WORK / endpoint).unlink(missing_ok=True)
    image(WORK / "root.qcow2", CONFIG["base"])
    if CONFIG.get("root_size"):
        subprocess.run(["qemu-img", "resize", str(WORK / "root.qcow2"), CONFIG["root_size"]], check=True)
    argv = ["qemu-system-x86_64", "-machine", "pc-i440fx-7.2", "-accel", "tcg,tb-size=128", "-cpu", CONFIG.get("cpu", "Nehalem"),
            "-smp", str(CONFIG.get("cpus", 2)), "-m", str(CONFIG.get("memory", 768)),
            "-display", "none", "-monitor", "none", "-no-shutdown",
            "-qmp", "unix:/work/qmp.sock,server=on,wait=off",
            "-chardev", "socket,id=serial0,path=/work/serial.sock,server=on,wait=off",
            "-serial", "chardev:serial0", "-device", "virtio-serial-pci",
            "-chardev", "socket,id=qga0,path=/work/qga.sock,server=on,wait=off",
            "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
            "-device", "virtio-scsi-pci,id=scsi0",
            "-drive", "if=none,id=root,file=/work/root.qcow2,format=qcow2",
            "-device", "virtio-blk-pci,drive=root,serial=nbq-root,bootindex=1",
            "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:18080-:{CONFIG.get('http_port', 8080)},hostfwd=tcp:127.0.0.1:12222-:22",
            "-device", "virtio-net-pci,netdev=net0", "-boot", "c"]
    if (WORK / "seed.iso").exists():
        argv += ["-drive", "file=/work/seed.iso,format=raw,if=ide,index=2,media=cdrom,readonly=on"]
    for number, disk in enumerate(CONFIG.get("disks", [])):
        ident = disk["id"]
        path = WORK / (ident + ".qcow2")
        image(path, disk.get("base"), disk.get("size") if not disk.get("base") else None)
        argv += ["-drive", f"if=none,id={ident},file={path},format=qcow2",
                 "-device", f"scsi-hd,drive={ident},id={ident},bus=scsi0.0,serial={disk['serial']}"]
    record("launch.jsonl", {"config": CONFIG, "argv": argv})
    err = (EVIDENCE / "qemu-stderr.log").open("ab", buffering=0)
    return subprocess.Popen(argv, stdout=err, stderr=err)


def terminate(signum, frame):
    STOP.set()
    try:
        QMP_CLIENT.call("quit", timeout=5)
    except Exception:
        pass
    if VM.poll() is None:
        VM.terminate()
    raise SystemExit(0)


if __name__ == "__main__":
    VM = start()
    threading.Thread(target=serial_worker, daemon=True).start()
    QMP_CLIENT = QMP()
    for path in ("/work/api.sock", "/tmp/nbq-console.sock"):
        Path(path).unlink(missing_ok=True)
    api = ThreadServer("/work/api.sock", APIHandler)
    os.chmod("/work/api.sock", 0o600)
    console = ThreadServer("/tmp/nbq-console.sock", ConsoleHandler)
    os.chmod("/tmp/nbq-console.sock", 0o666)
    threading.Thread(target=console.serve_forever, daemon=True).start()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    record("controller.jsonl", {"ready": True, "uid": os.getuid(), "qemu_pid": VM.pid})
    api.serve_forever()
