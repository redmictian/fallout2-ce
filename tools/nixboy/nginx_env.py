"""Normal-boot native nginx fixture on the existing confined QEMU runner."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import time

from qemu_env import QemuEnvironment, HERE, settings
from nginx_profile import (ORIGINS, PREPARATIONS, PUBLIC, STAFF, READY, PENDING,
                           DENIED, HOST_ID, configuration)

KIND = "qemu-nginx"
MANIFEST = HERE / "container/nginx/manifest.json"


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def preparation_identity(name):
    if name not in PREPARATIONS:
        raise ValueError("unknown fixed native-service preparation")
    digest = hashlib.sha256(name.encode())
    for path in (MANIFEST, HERE / "nginx_profile.py", HERE / "nginx_prepare.py", HERE / "nginx_probe.py"):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update(json.dumps(settings()).encode())
    return digest.hexdigest()


def digest_artifacts():
    digest = hashlib.sha256()
    for name in PREPARATIONS:
        digest.update(preparation_identity(name).encode())
    digest.update(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


class NginxEnvironment(QemuEnvironment):
    kind = KIND
    load_manifest = staticmethod(manifest)
    probe_origins = ORIGINS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The BT05-specific boot checkpoint is not this profile's observer API.
        del self.checkpoint
        self.preparation_id = self.params.get("preparation")
        self.preparation_identity = preparation_identity(self.preparation_id)
        self.initial_service = None

    def _probe(self):
        source = (HERE / "nginx_probe.py").read_text(encoding="utf-8")
        result = self.guest("/usr/bin/python3 -I -c " + shlex.quote(source), timeout=45)
        if result.get("exitcode") != 0 or result.get("truncated"):
            raise RuntimeError("native service probe unavailable: " + result.get("stderr", "")[-400:])
        value = json.loads(result["stdout"])
        self._last_probe, self._last_probe_at = value, time.monotonic()
        return value

    @staticmethod
    def _http_ok(response, status, body):
        return response.get("code") == 0 and response.get("status") == status \
            and response.get("body") == body and response.get("host_id") == HOST_ID

    def _facility_checks(self, value):
        checks = {"native_manager": value.get("pid1") == "systemd" and value.get("normal", {}).get("stdout") == "active",
                  "native_journal": bool(value.get("journal_socket")) and value.get("journal", {}).get("code") == 0,
                  "service_available": value.get("service", {}).get("ActiveState") == "active"}
        for role in ORIGINS:
            response = value.get("requests", {}).get(role, {}).get("/public.txt", {})
            # Reachability/expected server identity establish a capable client.
            # Wrong protected public bytes are an objective nonpass, not loss of
            # the facility. Actual request/response content is checked below.
            checks[role + "_capable_client"] = response.get("code") == 0 and response.get("status") == 200 \
                and response.get("host_id") == HOST_ID
        return checks

    def _decide(self, value, initial=False):
        checks = self._facility_checks(value)
        workers = value.get("workers", [])
        checks["nonprivileged_workers"] = bool(workers) and all(
            worker.get("uid") == [self.expected["worker_uid"]] * 4
            and worker.get("gid") == [self.expected["worker_gid"]] * 4
            and "nginx: worker process" in worker.get("cmd", "") for worker in workers)
        checks["enabled_service"] = value.get("service", {}).get("UnitFileState") == "enabled"
        checks["configuration_valid"] = value.get("nginx_test", {}).get("code") == 0
        checks["effective_configuration_observable"] = value.get("nginx_configuration", {}).get("code") == 0
        restricted = not initial or self.preparation_id == "nginx-reload-v1"
        stage_ready = not initial or self.preparation_id == "nginx-policy-v1"
        requests = value.get("requests", {})
        for role in ORIGINS:
            checks[role + "_public_body"] = self._http_ok(requests.get(role, {}).get("/public.txt", {}), 200, PUBLIC)
        checks["allowed_staff"] = self._http_ok(requests.get("allowed", {}).get("/staff.txt", {}), 200, STAFF)
        checks["denied_staff"] = self._http_ok(requests.get("denied", {}).get("/staff.txt", {}),
                                               403 if restricted else 200, DENIED if restricted else STAFF)
        checks["stage_response"] = all(self._http_ok(requests.get(role, {}).get("/stage2", {}),
            200 if stage_ready else 503, READY if stage_ready else PENDING) for role in ORIGINS)
        logs = value.get("logs", [])
        checks["distinct_client_sources"] = all(any(entry.get("probe") == requests.get(role, {}).get("/public.txt", {}).get("marker")
            and entry.get("source") == origin["address"] and entry.get("status") == 200 for entry in logs)
            for role, origin in ORIGINS.items())
        for name, text in (("public.txt", PUBLIC), ("staff.txt", STAFF), ("stage1.reference", "canonical stage one contract v1\n")):
            item = value.get("files", {}).get(name, {})
            checks["protected_" + name] = item.get("sha256") == hashlib.sha256(text.encode()).hexdigest() \
                and item.get("uid") == 0 and item.get("gid") == 0 and item.get("mode") == 0o644 and item.get("mtime") == 1768478400
        return checks

    def _compact(self, value, checks):
        return {"checks": checks, "preparation": self.preparation_identity,
                "preparation_id": self.preparation_id, "fixture": self.manifest["fixture"],
                "boot_id": value.get("boot_id"), "main_pid": value.get("service", {}).get("MainPID"),
                "worker_pids": [item["pid"] for item in value.get("workers", [])],
                "origins": {key: data["address"] for key, data in ORIGINS.items()}}

    def status(self):
        try:
            if not self._runner_state():
                raise RuntimeError("native guest runner stopped")
            machine = self._call("status", timeout=5)
            if machine.get("qemu_exit") is not None or machine.get("machine", {}).get("status") == "shutdown":
                return {"state": "off", "feedback": "Native guest is powered off; reissue the fixture."}
            serial = self._serial()
            if "Kernel panic - not syncing" in serial or "Oops:" in serial:
                raise RuntimeError("guest kernel failure")
            value = self._probe()
            facility = self._facility_checks(value)
            if not all(facility.values()):
                return {"state": "failure", "feedback": "Native facility unavailable: " + ", ".join(k for k, v in facility.items() if not v)}
            return {"state": "normal", "feedback": "Native manager, service and both independent clients are available."}
        except Exception as exc:
            return {"state": "failure", "feedback": str(exc)[:350]}

    def health(self):
        state = self.status()
        return {"status": "ready" if state["state"] == "normal" else "error", "feedback": state["feedback"],
                "facility_failure": state["state"] != "normal"}

    def start(self, cancel=None, progress=None):
        progress = progress or (lambda text: None)
        self._cancelled(cancel)
        try:
            self._launch_runner(progress)
            progress("Booting the prepared Linux kernel and native service manager...")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                self._cancelled(cancel)
                try:
                    result = self.guest("test \"$(cat /proc/1/comm)\" = systemd && systemctl is-active multi-user.target", timeout=7)
                    if result.get("exitcode") == 0:
                        break
                except Exception:
                    pass
                time.sleep(.5)
            else:
                raise TimeoutError("native guest normal startup unavailable")
            progress("Preparing fixed server and independent client namespaces...")
            data = {"preparation": self.preparation_id, "configuration": configuration(self.preparation_id),
                    "files": {"public.txt": PUBLIC, "staff.txt": STAFF, "stage1.reference": "canonical stage one contract v1\n"}}
            source = (HERE / "nginx_prepare.py").read_text(encoding="utf-8")
            result = self.guest("/usr/bin/python3 -I -c " + shlex.quote(source) + " " + shlex.quote(json.dumps(data)), timeout=45)
            if result.get("exitcode") != 0:
                raise RuntimeError("native preparation failed: " + result.get("stderr", "")[-700:])
            value = self._probe()
            checks = self._decide(value, initial=True)
            self._write("initial-native-probe.json", {"probe": value, "checks": checks})
            if not all(checks.values()):
                raise RuntimeError("native canonical preparation invalid: " + ", ".join(k for k, v in checks.items() if not v))
            self.initial_service = {"main": value["service"]["MainPID"], "workers": [item["pid"] for item in value["workers"]],
                                    "boot_id": value["boot_id"]}
            self._write("native-checkpoint.json", self.initial_service)
            brief = self.training_label + "\n" + self.spec.title + "\n\n" + self.spec.instructions.format(**self.params) + "\n"
            result = self.guest("printf %s " + shlex.quote(brief) + " > /root/CHALLENGE.txt", timeout=7)
            if result.get("exitcode") != 0:
                raise RuntimeError("native task handoff failed")
            self._cancelled(cancel)
            self._open_recovery_prompt(cancel)  # Same root-prompt readiness, normal boot.
            progress("Native nginx fixture ready; both clients and canonical fault were independently checked.")
            return self.container_id
        except Exception:
            self._archive_logs()
            self._remove()
            raise

    def check(self, cancel=None, progress=None):
        try:
            self._cancelled(cancel)
            value = self._probe()
            facility = self._facility_checks(value)
            if not all(facility.values()):
                return {"status": "error", "facility_failure": True, "feedback": "Native facility/client unavailable: " + ", ".join(k for k, v in facility.items() if not v)}
            checks = self._decide(value)
            self._write("last-native-validation.json", {"probe": value, "checks": checks})
            passed = all(checks.values())
            return {"status": "passed" if passed else "incomplete", "feedback": "Native nginx outcomes passed." if passed else
                    "Native nginx outcomes incomplete: " + ", ".join(k for k, v in checks.items() if not v),
                    "evidence": self._compact(value, checks)}
        except Exception as exc:
            return {"status": "error", "facility_failure": True, "feedback": "Native observation unavailable: " + str(exc)[:350]}

    def checkpoint(self, name):
        if name != "reload":
            raise ValueError("unsupported native checkpoint")
        result = self.check()
        if result["status"] != "passed":
            return result
        value = self._last_probe
        initial = self.initial_service or {}
        current = value["service"]
        checks = {"same_master": current.get("MainPID") == initial.get("main"),
                  "new_workers": bool(value["workers"]) and not set(initial.get("workers", [])) & {item["pid"] for item in value["workers"]},
                  "same_boot": value["boot_id"] == initial.get("boot_id"),
                  "native_reload": "status=0" in current.get("ExecReload", "") and bool(re.search(r"\bpid=[1-9][0-9]*", current.get("ExecReload", "")))
                    and "Reload" in value.get("journal", {}).get("stdout", "")}
        passed = all(checks.values())
        evidence = {"checks": checks, "main_pid": current.get("MainPID"), "worker_pids": [item["pid"] for item in value["workers"]],
                    "preparation": self.preparation_identity, "preparation_id": self.preparation_id, "boot_id": value["boot_id"]}
        self._write("last-reload-checkpoint.json", evidence)
        return {"status": "passed" if passed else "incomplete", "feedback": "Native service reload observed." if passed else
                "A native reload with unchanged master and replaced workers is required; restart is different.", "evidence": evidence}
