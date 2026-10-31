"""One optional Docker-managed BT05 guest, using the ordinary attempt contract.

Machine control, guest probes and learner serial attachment are separate. The
host retains expected identities/checkpoints/evidence; guest root is a willing
learner, not a hostile attestation boundary. No marker grants a completion.
"""
import hashlib
import base64
import json
import os
from pathlib import Path
import re
import random
import shlex
import subprocess
import threading
import time

from challenge_env import TRAINING_LABEL, OWNER_LABEL, ATTEMPT_LABEL, owner_namespace

QEMU_KIND = "qemu-bt05"
HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "container" / "qemu"
ROLE_LABEL = "org.fallout2-ce.nixboy.qemu"
DEFAULT_IMAGE = "nixboy-qemu-bt05:20261030"
DEFAULT_VOLUME = "nbq-20261030-images"
BOOT_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def settings():
    image = os.environ.get("NIXBOY_QEMU_IMAGE", DEFAULT_IMAGE)
    volume = os.environ.get("NIXBOY_QEMU_IMAGES_VOLUME", DEFAULT_VOLUME)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,254}", image):
        raise ValueError("invalid QEMU operator image name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", volume):
        raise ValueError("invalid QEMU artifact volume name")
    return image, volume


def manifest():
    return json.loads((ARTIFACTS / "bt05-manifest.json").read_text(encoding="utf-8"))


def digest_artifacts():
    digest = hashlib.sha256()
    for path in sorted(ARTIFACTS.iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update((HERE / "qemu_probe.py").read_bytes().replace(b"\r\n", b"\n"))
    digest.update(json.dumps(settings()).encode())
    return digest.hexdigest()


def cleanup_qemu_owned(client, state_dir):
    """Complete generic training cleanup: only this owner's QEMU work volumes."""
    labels = [TRAINING_LABEL + "=1", OWNER_LABEL + "=" + owner_namespace(state_dir), ROLE_LABEL + "=work"]
    for value in client.api.volumes(filters={"label": labels}).get("Volumes") or []:
        client.api.remove_volume(value["Name"])


class QemuEnvironment:
    persistent = True
    kind = QEMU_KIND
    load_manifest = staticmethod(manifest)

    def __init__(self, client, spec, attempt_id, seed, generation, state_dir, params=None, runner=None,
                 content_root=None, training_label=""):
        if spec.kind != self.kind:
            raise ValueError("unsupported guest kind")
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", attempt_id):
            raise ValueError("invalid attempt identifier")
        if type(generation) is not int or not 0 <= generation <= 2**31 - 1:
            raise ValueError("invalid generation")
        self.client, self.spec = client, spec
        self.attempt_id, self.generation, self.seed = attempt_id, generation, seed
        self.state_dir = Path(state_dir).resolve()
        self.owner = owner_namespace(state_dir)
        self.name = f"nixboy_qemu_{self.owner[:8]}_{hashlib.sha256(attempt_id.encode()).hexdigest()[:12]}_{generation}"
        self.volume = self.name + "_work"
        self.evidence = self.state_dir / "qemu-evidence" / attempt_id / str(generation)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.image, self.images_volume = settings()
        self.manifest = self.load_manifest()
        self.expected = self.manifest["expected"]
        choices = getattr(spec, "parameters", {})
        if params is None:
            rng = random.Random(seed)
            params = {key: rng.choice(values) for key, values in sorted(choices.items())}
        if set(params) != set(choices) or any(value not in choices[key] for key, value in params.items()):
            raise ValueError("parameters do not match the authored choices")
        self.params = dict(params)
        self.training_label = training_label
        self.container_id = None
        self._closed = False
        self.checkpoint = None
        self._last_probe = None
        self._last_probe_at = 0
        self._created_at = 0
        self._cancel = threading.Event()
        self._console_sized = False

    def attachment(self):
        return {"kind": "qemu-serial", "user": "1001:1001",
                "argv": ["python3", "/opt/nixboy-qemu/control.py", "console"]}

    def set_console_size(self, rows, cols, cancel=None):
        """Set geometry at the exclusive first handoff, never into a resumed job."""
        if self._console_sized:
            return
        if type(rows) is not int or type(cols) is not int or not 10 <= rows <= 200 or not 20 <= cols <= 300:
            raise ValueError("invalid guest console dimensions")
        marker = "NB_SIZE_" + os.urandom(6).hex()
        self._call("serial-send", f"stty rows {rows} cols {cols}; printf '\\n{marker}\\n'\r", timeout=5)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self._cancelled(cancel)
            if re.search(r"\r?\n" + marker + r"\r?\n", self._serial()):
                self._console_sized = True
                self._write("console-size.json", {"rows": rows, "cols": cols, "scope": "first handoff only"})
                return
            time.sleep(.2)
        raise TimeoutError("guest console geometry setup did not complete")

    def _write(self, name, value):
        target = self.evidence / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _cancelled(self, cancel=None):
        if self._cancel.is_set() or cancel is not None and cancel.is_set():
            raise RuntimeError("Guest operation cancelled; reset/reissue when ready")

    def _exec(self, argv, timeout=15):
        if not self.container_id:
            raise RuntimeError("guest runner unavailable; reset to reissue")
        command = ["/usr/bin/timeout", "--signal=TERM", "--kill-after=1", str(timeout + 2), *argv]
        execution = self.client.api.exec_create(self.container_id, command, user="1000:1000",
                                                 stdout=True, stderr=True, workdir="/work")
        response = self.client.api.exec_start(execution["Id"], stream=True, demux=True)
        out, err = bytearray(), bytearray()
        try:
            for a, b in response:
                out.extend(a or b"")
                err.extend(b or b"")
                if len(out) + len(err) > 1048576:
                    raise RuntimeError("guest probe output exceeded limit")
        finally:
            if hasattr(response, "close"):
                response.close()
        rc = self.client.api.exec_inspect(execution["Id"])["ExitCode"]
        if rc in (None, 124, 137):
            raise TimeoutError("guest controller timeout")
        return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    def _call(self, method, value=None, timeout=12):
        argv = ["python3", "/opt/nixboy-qemu/control.py", method]
        if value is not None:
            argv.append(value)
        argv += ["--timeout", str(timeout)]
        rc, out, err = self._exec(argv, timeout + 5)
        try:
            result = json.loads(out)
        except ValueError as exc:
            raise RuntimeError("Guest controller unavailable: " + err[-300:]) from exc
        if rc or not result.get("ok"):
            raise RuntimeError(result.get("error", err[-300:]))
        return result["result"]

    def guest(self, script, timeout=15):
        return self._call("guest", script, timeout)

    def qmp(self, command, arguments=None):
        return self._call("qmp", json.dumps({"execute": command, "arguments": arguments}) if arguments is not None
                          else json.dumps({"execute": command}), timeout=5)

    def _runner_state(self):
        if not self.container_id:
            return False
        return bool(self.client.api.inspect_container(self.container_id)["State"]["Running"])

    def _serial(self):
        return self._call("serial-read", timeout=5)

    def _open_recovery_prompt(self, cancel=None):
        deadline, next_enter = time.monotonic() + 30, 0
        while time.monotonic() < deadline:
            self._cancelled(cancel)
            serial = self._serial()
            if re.search(r"root@[^\r\n]*[#$]", serial):
                return
            # systemd prints 'emergency mode' before sulogin owns the TTY.
            # A premature Enter can be discarded when sulogin configures it.
            # Retry harmless empty input only during this exclusive preflight.
            if "Control-D to continue):" in serial and time.monotonic() >= next_enter:
                self._call("serial-send", "\r", timeout=5)
                next_enter = time.monotonic() + 2
            time.sleep(.2)
        raise TimeoutError("serial recovery did not open a usable root prompt")

    def _probe(self):
        source = (HERE / "qemu_probe.py").read_text(encoding="utf-8")
        result = self.guest("/usr/bin/python3 -I -c " + shlex.quote(source), timeout=18)
        if result.get("exitcode") != 0 or result.get("truncated"):
            raise RuntimeError("Guest observation probe failed: " + result.get("stderr", "")[-500:])
        value = json.loads(result["stdout"])
        self._last_probe, self._last_probe_at = value, time.monotonic()
        return value

    def status(self):
        try:
            if not self._runner_state():
                return {"state": "failure", "feedback": "Guest provider/runner stopped. Reset to reissue; no learner failure recorded."}
            machine = self._call("status", timeout=5)
            serial = self._serial()
            if "Kernel panic - not syncing" in serial or "Oops:" in serial:
                return {"state": "failure", "feedback": "Guest kernel failure. Diagnostics retained; reset to reissue."}
            if machine.get("qemu_exit") is not None:
                return {"state": "failure", "feedback": "Guest emulator exited; reset to reissue."}
            if machine["machine"]["status"] == "shutdown":
                return {"state": "off", "feedback": "Guest powered off. Reset reissues the canonical attempt."}
            latest_kernel = serial.rfind("Linux version ")
            if max(serial.rfind("emergency mode"), serial.rfind("maintenance")) > latest_kernel:
                return {"state": "recovery", "feedback": "Expected mount recovery; repair fstab and reboot the guest."}
            try:
                probe = self._probe()
                if probe["normal"]["stdout"] == "active":
                    return {"state": "normal", "feedback": "Guest normal startup reached."}
                if probe["emergency"]["stdout"] == "active":
                    return {"state": "recovery", "feedback": "Expected mount recovery; repair fstab and reboot the guest."}
            except Exception:
                # The agent need not start in the canonical emergency boot.
                pass
            # Old recovery text remains in console history; compare with the
            # latest kernel banner rather than treating any old prompt as live.
            resets = self._reboot_events()
            recent_reset = resets and time.time() - resets[-1]["time"] < 180
            if recent_reset or self._created_at and time.monotonic() - self._created_at < 300:
                return {"state": "booting", "feedback": "Guest boot/probe transport is not ready; wait and retry."}
            return {"state": "failure", "feedback": "Guest probe transport remains unavailable. Reset/reissue; no learner failure recorded."}
        except Exception as exc:
            return {"state": "failure", "feedback": "Guest facility unavailable: " + str(exc)[:250]}

    def _launch_runner(self, progress=None, network="none"):
        """Shared confined machine launch; guest readiness belongs to its profile."""
        if network not in ("none", "bridge"):
            raise ValueError("unsupported fixed runner network")
        progress = progress or (lambda value: None)
        labels = {TRAINING_LABEL: "1", OWNER_LABEL: self.owner, ATTEMPT_LABEL: self.attempt_id,
                  ROLE_LABEL: "runner", "org.fallout2-ce.nixboy.generation": str(self.generation)}
        progress("Checking pinned guest artifacts and creating disposable overlays...")
        self.client.api.inspect_volume(self.images_volume)
        self.client.api.inspect_image(self.image)
        v = self.client.api.create_volume(self.volume, labels={**labels, ROLE_LABEL: "work"})
        if v.get("Labels", {}).get(OWNER_LABEL) != self.owner:
            raise RuntimeError("guest work-volume ownership collision")
        config = {"base": "/images/" + self.manifest["root"], "hashes": self.manifest["hashes"],
                  "memory": self.manifest["memory_mib"], "cpus": self.manifest["cpus"],
                  "cpu": self.manifest["cpu"], "disks": self.manifest["disks"]}
        host = self.client.api.create_host_config(
            binds={self.images_volume: {"bind": "/images", "mode": "ro"},
                   self.volume: {"bind": "/work", "mode": "rw"},
                   str(self.evidence): {"bind": "/evidence", "mode": "rw"}},
            network_mode=network, cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
            read_only=True, mem_limit=2 * 1024**3, memswap_limit=2 * 1024**3,
            nano_cpus=3 * 10**9, pids_limit=256, tmpfs={"/tmp": "mode=1777"})
        created = self.client.api.create_container(self.image, name=self.name, user="1000:1000",
            labels=labels, environment={"VM_CONFIG": json.dumps(config)}, host_config=host)
        self.container_id = created["Id"]
        self.client.api.start(self.container_id)
        self._created_at = time.monotonic()
        self._write("runner-inspect.json", self.client.api.inspect_container(self.container_id))

    def start(self, cancel=None, progress=None):
        self._cancelled(cancel)
        progress = progress or (lambda value: None)
        try:
            self._launch_runner(progress)
            progress("Runner started. Booting the guest kernel into the intended mount recovery...")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                self._cancelled(cancel)
                if not self._runner_state():
                    raise RuntimeError("guest runner failed startup; inspect retained evidence")
                try:
                    serial = self._serial()
                    if "Kernel panic - not syncing" in serial:
                        raise RuntimeError("guest kernel panicked before recovery")
                    if "Control-D to continue):" in serial:
                        break
                except RuntimeError as exc:
                    if "panicked" in str(exc):
                        raise
                if cancel is not None:
                    cancel.wait(.5)
                else:
                    time.sleep(.5)
            else:
                raise TimeoutError("guest did not reach the expected recovery console")
            progress("Expected recovery reached. Capturing the initial guest boot identity...")
            self._open_recovery_prompt(cancel)
            nonce = "NB_BOOT_" + os.urandom(6).hex()
            # The exclusive preflight supplies the readable task, not a screenful
            # of its transport encoding. Restore ordinary guest echo at handoff.
            echo_marker = "NB_ECHO_" + os.urandom(6).hex()
            self._call("serial-send", "stty -echo; printf '\\n" + echo_marker + "\\n'\r", timeout=5)
            echo_deadline = time.monotonic() + 15
            while time.monotonic() < echo_deadline:
                self._cancelled(cancel)
                if re.search(r"\r?\n" + echo_marker + r"\r?\n", self._serial()):
                    break
                time.sleep(.2)
            else:
                raise TimeoutError("serial preflight did not acknowledge its console setup")
            instructions = getattr(self.spec, "instructions", "Repair the required mount UUID and reboot the guest.").format(**self.params)
            brief = self.training_label + "\n" + getattr(self.spec, "title", "BT05-A") + "\n\n" + instructions + "\n"
            encoded = base64.b64encode(brief.encode("utf-8")).decode("ascii")
            if len(encoded) > 3000:
                raise ValueError("BT05 console task exceeds the first-slice serial line budget")
            command = "printf %s " + shlex.quote(encoded) + " | /usr/bin/base64 -d > /root/CHALLENGE.txt; "
            command += "stty rows 24 cols 80 echo; printf '\\n" + nonce + ":'; cat /proc/sys/kernel/random/boot_id\r"
            self._call("serial-send", command, timeout=5)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                self._cancelled(cancel)
                serial = self._serial()
                found = re.search(r"(?:\r?\n)" + nonce + r":(" + BOOT_ID + r")\r?\n", serial)
                if found:
                    self.checkpoint = {"initial_boot_id": found.group(1), "captured_at": time.time(),
                                       "fixture": self.manifest["fixture"], "generation": self.generation,
                                       "serial_offset": (self.evidence / "serial.log").stat().st_size}
                    self._write("checkpoint.json", self.checkpoint)
                    progress("Recovery console ready. Repair the required UUID, then reboot the guest.")
                    return self.container_id
                time.sleep(.2)
            raise TimeoutError("recovery console boot-identity capture failed")
        except Exception:
            self._archive_logs()
            self._remove()
            raise

    def _reboot_events(self):
        if not self.checkpoint:
            return []
        path = self.evidence / "qmp-events.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                if value.get("event") == "RESET" and value.get("data", {}).get("guest") \
                        and value.get("time", 0) > self.checkpoint["captured_at"]:
                    events.append(value)
            except ValueError:
                continue  # An in-progress final line is not a completed event.
        return events

    def _http(self):
        code = """import hashlib,json,urllib.request,urllib.error
try:
 r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:18080/sentinel.txt',timeout=5)
except urllib.error.HTTPError as e: r=e
body=r.read(1048576)
print(json.dumps({'status':r.code,'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}))
"""
        rc, out, err = self._exec(["python3", "-I", "-c", code], timeout=8)
        if rc:
            return {"error": err[-500:]}
        return json.loads(out)

    def _decide(self, probe, http, events, serial):
        e = self.expected
        try:
            mounted = json.loads(probe["mount"]["stdout"])["filesystems"][0]
        except (ValueError, KeyError, IndexError):
            mounted = {}
        lines = probe["fstab"].splitlines()
        try:
            rows = [shlex.split(line, comments=True) for line in lines if line.strip() and not line.lstrip().startswith("#")]
        except ValueError:
            rows = []  # A malformed learner fstab is an incomplete outcome.
        entries = [row for row in rows if len(row) >= 2 and row[1] == e["mount"]]
        mandatory = len(entries) == 1 and len(entries[0]) == 6 and entries[0][0] == "UUID=" + e["data_uuid"] \
            and entries[0][2] == "ext4" and entries[0][4:] == ["0", "2"] \
            and not set(entries[0][3].split(",")) & {"nofail", "noauto", "x-systemd.automount", "ro"}
        payload = probe["payload"]
        service, worker = probe["service"], probe["worker"]
        worker_uid = re.search(r"^Uid:\s+(\d+)\s+(\d+)", worker.get("status", ""), re.MULTILINE)
        checks = {
            "genuine_reboot": bool(self.checkpoint and events and probe["boot_id"] != self.checkpoint["initial_boot_id"]
                and "Linux version " + self.manifest["guest"].split(" / ")[1] in serial),
            "normal_startup": probe["normal"]["stdout"] == "active",
            "expected_filesystem": mounted.get("uuid") == e["data_uuid"] and mounted.get("fstype") == "ext4"
                and mounted.get("target") == e["mount"] and "rw" in mounted.get("options", "").split(","),
            "mandatory_fstab": mandatory,
            "unrelated_fstab": [line for line in lines if e["mount"] not in line] == e["fstab_unrelated"],
            "protected_payload": payload.get("sha256") == e["payload_sha256"] and payload.get("size") == e["payload_bytes"],
            "protected_metadata": ":".join(str(payload.get(k)) for k in ("uid", "gid", "mode", "mtime"))
                == "0:0:420:1768478400",
            "protected_root_peer": probe["root_peer"].get("sha256") == e["root_peer_sha256"],
            "service": service.get("ActiveState") == "active" and service.get("UnitFileState") == "enabled"
                and service.get("User") == "nobody" and worker_uid is not None and worker_uid.group(2) == "65534"
                and "-m http.server 8080" in worker.get("cmdline", ""),
            "outside_http": http.get("status") == 200 and http.get("sha256") == e["payload_sha256"]
                and http.get("bytes") == e["payload_bytes"],
        }
        return checks

    def check(self, cancel=None, progress=None):
        try:
            self._cancelled(cancel)
            state = self.status()
            if state["state"] != "normal":
                return {"status": "error", "feedback": state["feedback"], "facility_state": state["state"],
                        "facility_failure": state["state"] == "failure"}
            if progress:
                progress("Checking independent reboot, disk identity, protected data and HTTP service...")
            probe = self._probe()
            self._cancelled(cancel)
            http, events = self._http(), self._reboot_events()
            # Full external serial evidence survives the broker's bounded replay
            # ring. Only bytes after the trusted initial checkpoint count.
            with (self.evidence / "serial.log").open("rb") as source:
                source.seek((self.checkpoint or {}).get("serial_offset", 0))
                serial = source.read(4 * 1024 * 1024).decode("utf-8", "replace")
            checks = self._decide(probe, http, events, serial)
            evidence = {"checks": checks, "probe": probe, "http": http, "reboot_events": events,
                        "checkpoint": self.checkpoint, "observed_at": time.time()}
            self._write("last-validation.json", evidence)
            passed = all(checks.values())
            return {"status": "passed" if passed else "incomplete", "feedback":
                "Real guest reboot and all protected state/service checks passed." if passed else
                "Required guest outcomes are incomplete: " + ", ".join(k for k, v in checks.items() if not v),
                "evidence": {"checks": checks, "boot_id": probe["boot_id"], "fixture": self.manifest["fixture"]}}
        except Exception as exc:
            return {"status": "error", "feedback": "Guest facility/validator unavailable: " + str(exc)[:500]}

    def _archive_logs(self):
        if self.container_id:
            try:
                logs = self.client.api.logs(self.container_id, stdout=True, stderr=True, tail=100)
                (self.evidence / "runner.log").write_bytes(logs)
            except Exception:
                pass

    def _remove(self):
        if self.container_id:
            try:
                data = self.client.api.inspect_container(self.container_id)
                if data["Config"]["Labels"].get(OWNER_LABEL) != self.owner:
                    raise RuntimeError("guest container ownership mismatch")
                self.client.api.remove_container(self.container_id, force=True, v=True)
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
            finally:
                self.container_id = None
        try:
            value = self.client.api.inspect_volume(self.volume)
            if value.get("Labels", {}).get(OWNER_LABEL) == self.owner and value.get("Labels", {}).get(ROLE_LABEL) == "work":
                self.client.api.remove_volume(self.volume)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise

    def close(self):
        if self._closed:
            return
        self._cancel.set()
        try:
            self._archive_logs()
            self._remove()
        finally:
            self._closed = True
            self.client.close()
