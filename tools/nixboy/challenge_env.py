"""Host-owned Compose exercises and trusted, state-based assessment.

No learner container receives host mounts, a Docker socket, or the validator.
Compose is invoked with argv, fixed topology files, an explicit local daemon,
bounded execution and attempt-specific ownership labels.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from challenge_content import CONTENT_ROOT, KINDS

TRAINING_LABEL = "org.fallout2-ce.nixboy.training"
OWNER_LABEL = "org.fallout2-ce.nixboy.owner"
ATTEMPT_LABEL = "org.fallout2-ce.nixboy.attempt"
PERMISSIONS = {"private-file": 0o600, "public-read": 0o644, "executable": 0o700,
               "shared-directory": 0o770, "group-readable": 0o640}


def owner_namespace(state_dir):
    return hashlib.sha256(str(Path(state_dir).resolve()).encode("utf-8")).hexdigest()[:16]


def cleanup_owned(client, state_dir):
    """Reconcile only this bridge state namespace, never unrelated Docker resources."""
    labels = [f"{TRAINING_LABEL}=1", f"{OWNER_LABEL}={owner_namespace(state_dir)}"]
    errors = []
    for item in client.api.containers(all=True, filters={"label": labels}):
        try:
            client.api.remove_container(item["Id"], force=True, v=True)
        except Exception as exc:
            errors.append(str(exc))
    for item in client.api.networks(filters={"label": labels}):
        try:
            client.api.remove_network(item["Id"])
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("owned exercise cleanup failed: " + "; ".join(errors))


class ComposeEnvironment:
    def __init__(self, client, spec, attempt_id, seed, generation, state_dir, params=None, runner=None,
                 content_root=None, training_label=""):
        if spec.kind not in KINDS:
            raise ValueError("unsupported challenge kind")
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", attempt_id):
            raise ValueError("invalid attempt identifier")
        if type(generation) is not int or not 0 <= generation <= 2**31 - 1:
            raise ValueError("invalid attempt generation")
        self.client, self.spec = client, spec
        self.attempt_id, self.seed, self.generation = attempt_id, seed, generation
        self.state_dir = Path(state_dir).resolve()
        self.owner = owner_namespace(state_dir)
        self.project = f"nixboy_{self.owner[:8]}_{hashlib.sha256(attempt_id.encode()).hexdigest()[:12]}_{generation}"
        if params is None:
            import random
            rng = random.Random(seed)
            params = {key: rng.choice(values) for key, values in sorted(spec.parameters.items())}
        if set(params) != set(spec.parameters) or any(value not in spec.parameters[key] for key, value in params.items()):
            raise ValueError("parameters do not match the authored choices")
        self.params = dict(params)
        self.training_label = training_label
        self.network = spec.kind.startswith("ssh-")
        self.content_root = Path(content_root if content_root is not None else CONTENT_ROOT).resolve()
        self.compose_file = self.content_root / "compose" / ("network.yaml" if self.network else "single.yaml")
        self.runner = runner or subprocess.run
        self.container_id = None
        self.services = {}
        self._closed = False
        self.payload = ("#!/bin/sh\nprintf 'maintenance ready\\n'\n" if spec.kind == "executable" else
                        f"Nix-Boy {self.params['name']} report.\nPreserve this complete text.\n")
        self.expected = None

    def _compose(self, *arguments, timeout=45):
        # Preserve PATH/runtime basics but do not inherit alternate Compose files,
        # remote Docker contexts, credential injection or arbitrary interpolation.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("DOCKER_", "COMPOSE_", "NIXBOY_"))}
        env.update(NIXBOY_OWNER=self.owner, NIXBOY_ATTEMPT=self.attempt_id,
                   NIXBOY_GENERATION=str(self.generation))
        for key in ("NIXBOY_TRAINING_IMAGE", "NIXBOY_SSH_IMAGE"):
            if key in os.environ:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,254}", os.environ[key]):
                    raise ValueError("invalid operator image name")
                env[key] = os.environ[key]
        endpoint = "npipe:////./pipe/docker_engine" if os.name == "nt" else "unix:///var/run/docker.sock"
        result = self.runner(["docker", "--host", endpoint, "compose", "--project-name", self.project,
                              "--file", str(self.compose_file), *arguments],
                             env=env, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError("Compose operation failed: " + (result.stderr or result.stdout)[-2000:])
        return result.stdout.strip()

    def _exec(self, service, argv, user="1000:1000"):
        # Immutable image tools bound runtime and output even when learner shell
        # startup/configuration has been changed to hang or print indefinitely.
        bounded = ["/usr/bin/timeout", "--signal=TERM", "--kill-after=1", "7", *argv]
        execution = self.client.api.exec_create(self.services[service], bounded, user=user,
                                                workdir="/home/nixboy", environment={"LC_ALL": "C"})
        output = self.client.api.exec_start(execution["Id"], stream=True, demux=True)
        stdout, stderr = bytearray(), bytearray()
        try:
            for out, err in output:
                stdout.extend(out or b"")
                stderr.extend(err or b"")
                if len(stdout) + len(stderr) > 131072:
                    raise RuntimeError("exercise probe exceeded output limit")
        finally:
            if hasattr(output, "close"):
                output.close()
        status = self.client.api.exec_inspect(execution["Id"])["ExitCode"]
        if status in (124, 137, None):
            raise RuntimeError("exercise probe timed out")
        return status, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")

    def _python(self, service, code, data=None, user="1000:1000"):
        result, output, error = self._exec(service, ["/usr/bin/python3", "-I", "-c", code, json.dumps(data)], user=user)
        if result:
            raise RuntimeError("trusted exercise probe failed: " + error[-1000:])
        return json.loads(output) if output else None

    def _write(self, service, path, text, mode=0o644):
        self._python(service, """import json,os,sys
from pathlib import Path
d=json.loads(sys.argv[1]); p=Path(d['path']); p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(d['text'],encoding='utf-8'); p.chmod(d['mode'])
print('null')
""", {"path": path, "text": text, "mode": mode})

    def _mkdir(self, service, path, mode=0o755):
        self._python(service, """import json,sys
from pathlib import Path
d=json.loads(sys.argv[1]); p=Path(d['path']); p.mkdir(parents=True,exist_ok=True); p.chmod(d['mode']); print('null')
""", {"path": path, "mode": mode})

    def snapshot(self, service, paths):
        return self._python(service, """import json,stat,sys
from pathlib import Path
result={}
for name in json.loads(sys.argv[1]):
 p=Path(name)
 try: s=p.lstat()
 except FileNotFoundError: result[name]=None; continue
 except (PermissionError,NotADirectoryError): result[name]={'mode':None,'uid':None,'gid':None,'directory':False,'regular':False,'text':None}; continue
 item={'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,
       'directory':stat.S_ISDIR(s.st_mode),'regular':stat.S_ISREG(s.st_mode)}
 if item['regular'] and s.st_size<=65536:
  try: item['text']=p.read_text(encoding='utf-8')
  except (UnicodeError,PermissionError): item['text']=None
 result[name]=item
print(json.dumps(result))
""", paths)

    def start(self):
        if self._closed:
            raise RuntimeError("exercise client is closed")
        try:
            self._compose("up", "--wait", "--wait-timeout", "25", "--pull", "never", "--no-build")
            for service in (("workstation", "server") if self.network else ("workstation",)):
                identifier = self._compose("ps", "--quiet", service, timeout=10)
                if not re.fullmatch(r"[a-f0-9]{12,64}", identifier):
                    raise RuntimeError("Compose did not return one service container")
                self.services[service] = identifier
            self.container_id = self.services["workstation"]
            self._setup()
            initial = self.check()
            if initial["status"] != "incomplete":
                raise RuntimeError("exercise preflight must establish an unsolved fixture: " + str(initial))
            return self.container_id
        except Exception:
            self._teardown()
            raise

    def _setup(self):
        name, kind = self.params["name"], self.spec.kind
        lab = "/home/nixboy/lab"
        self._mkdir("workstation", lab)
        task = self.spec.instructions.format(**self.params)
        self._write("workstation", "/home/nixboy/CHALLENGE.txt",
                    (self.training_label + "\n" if self.training_label else "") + self.spec.title + "\n\n" + task + "\n")
        self._write("workstation", "/home/nixboy/practice.txt", "Practice here. The lab task is separate.\n")
        if kind == "make-directory":
            self._write("workstation", lab + "/briefing.txt", self.payload)
        elif kind in ("copy-file", "move-file"):
            self._write("workstation", f"{lab}/incoming/{name}.txt", self.payload)
            self._mkdir("workstation", lab + ("/delivery" if kind == "copy-file" else "/archive"))
        elif kind == "rename-directory":
            self._write("workstation", f"{lab}/{name}-draft/report.txt", self.payload)
        elif kind == "remove-empty":
            self._mkdir("workstation", f"{lab}/{name}")
            self._write("workstation", lab + "/records/briefing.txt", self.payload)
        elif kind in PERMISSIONS:
            if kind == "shared-directory":
                self._write("workstation", f"{lab}/{name}/note.txt", self.payload)
                self._mkdir("workstation", f"{lab}/{name}", 0o700)
            else:
                suffix = "sh" if kind == "executable" else "txt"
                self._write("workstation", f"{lab}/{name}.{suffix}", self.payload, 0o666)
        elif kind in ("filter-lines", "sort-unique", "extract-field", "count-matches", "replace-text"):
            subject = self.params.get("subject", "station")
            if kind == "filter-lines":
                source = f"OK {subject} ready\nERROR {subject} blocked\nINFO no ERROR here\nERROR {subject} stopped\n"
                self.expected = f"ERROR {subject} blocked\nERROR {subject} stopped\n"
            elif kind == "sort-unique":
                source = f"zulu\n{subject}\nalpha\n{subject}\nalpha\n"
                self.expected = "\n".join(sorted({"zulu", subject, "alpha"})) + "\n"
            elif kind == "extract-field":
                channel = self.params["channel"]
                source = f"north:{channel}:active\nsouth:{channel+1}:idle\neast:{channel+2}:active\n"
                self.expected = f"{channel}\n{channel+1}\n{channel+2}\n"
            elif kind == "count-matches":
                source = f"WARN {subject} hot\nOK ready\nWARN {subject} slow\nINFO WARN is a label\nWARN {subject} cold\n"
                self.expected = "3\n"
            else:
                source = f"{subject}=offline\nbackup=offline,offline\ncomment=keep-this\n"
                self.expected = source.replace("offline", "online")
            suffix = "log" if kind in ("filter-lines", "count-matches") else "conf" if kind == "replace-text" else "txt"
            self.source_path, self.source_text = f"{lab}/{name}.{suffix}", source
            self._write("workstation", self.source_path, source)
        elif self.network:
            self._setup_network()

    def _setup_network(self):
        name, kind = self.params["name"], self.spec.kind
        self._mkdir("workstation", "/home/nixboy/.ssh", 0o700)
        code, key, error = self._exec("workstation", ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", "/home/nixboy/.ssh/id_ed25519"])
        if code:
            raise RuntimeError("cannot create exercise SSH key: " + error)
        public = self.snapshot("workstation", ["/home/nixboy/.ssh/id_ed25519.pub"])["/home/nixboy/.ssh/id_ed25519.pub"]["text"]
        self._mkdir("server", "/home/nixboy/.ssh", 0o700)
        if kind != "ssh-key-login":
            self._write("server", "/home/nixboy/.ssh/authorized_keys", public, 0o600)
        code, host_key, error = self._exec("server", ["/bin/cat", "/etc/ssh/ssh_host_ed25519_key.pub"])
        if code:
            raise RuntimeError("cannot inspect server host key: " + error)
        self._write("workstation", "/home/nixboy/.ssh/known_hosts", "server " + host_key, 0o600)
        if kind == "ssh-upload":
            self._write("workstation", f"/home/nixboy/lab/{name}.txt", self.payload)
        elif kind == "ssh-download":
            self._write("server", f"/home/nixboy/{name}.txt", self.payload)
        elif kind == "ssh-remote-move":
            self._write("server", f"/home/nixboy/incoming/{name}.txt", self.payload)
            self._mkdir("server", "/home/nixboy/archive")

    def check(self):
        try:
            if not self.services:
                raise RuntimeError("exercise is not running")
            for identifier in self.services.values():
                if not self.client.api.inspect_container(identifier)["State"]["Running"]:
                    raise RuntimeError("an exercise service has stopped")
            ok, feedback = self._validate()
            return {"status": "passed" if ok else "incomplete", "feedback": feedback}
        except Exception as exc:
            return {"status": "error", "feedback": "Exercise validation unavailable: " + str(exc)[:1000]}

    def _validate(self):
        name, kind = self.params["name"], self.spec.kind
        lab = "/home/nixboy/lab"
        def observe(service, *paths):
            values = self.snapshot(service, list(paths))
            return [values[path] for path in paths]
        def text(item, expected):
            return bool(item and item["regular"] and item.get("text") == expected)
        def directory(item):
            return bool(item and item["directory"])
        if kind == "make-directory":
            target, preserved = observe("workstation", f"{lab}/{name}/ready", lab + "/briefing.txt")
            ok = directory(target) and text(preserved, self.payload)
        elif kind == "copy-file":
            source, target = observe("workstation", f"{lab}/incoming/{name}.txt", lab + "/delivery/manifest.txt")
            ok = text(source, self.payload) and text(target, self.payload)
        elif kind == "move-file":
            source, target = observe("workstation", f"{lab}/incoming/{name}.txt", f"{lab}/archive/{name}.txt")
            ok = source is None and text(target, self.payload)
        elif kind == "rename-directory":
            source, target, report = observe("workstation", f"{lab}/{name}-draft", f"{lab}/{name}-final", f"{lab}/{name}-final/report.txt")
            ok = source is None and directory(target) and text(report, self.payload)
        elif kind == "remove-empty":
            target, preserved = observe("workstation", f"{lab}/{name}", lab + "/records/briefing.txt")
            ok = target is None and text(preserved, self.payload)
        elif kind in PERMISSIONS:
            suffix = "sh" if kind == "executable" else "txt"
            path = f"{lab}/{name}" if kind == "shared-directory" else f"{lab}/{name}.{suffix}"
            target = observe("workstation", path)[0]
            ok = bool(target and target["mode"] == PERMISSIONS[kind] and target["uid"] == 1000 and target["gid"] == 1000)
            if kind == "shared-directory":
                ok = ok and directory(target) and text(observe("workstation", path + "/note.txt")[0], self.payload)
            else:
                ok = ok and text(target, self.payload)
        elif kind in ("filter-lines", "sort-unique", "extract-field", "count-matches", "replace-text"):
            source, target = observe("workstation", self.source_path, lab + "/result.txt")
            ok = text(source, self.source_text) and text(target, self.expected)
            if kind in ("filter-lines", "sort-unique", "extract-field"):
                # A final newline is a file convention, not the competency here.
                ok = text(source, self.source_text) and bool(target and target["regular"] and
                     target.get("text") is not None and target["text"].splitlines() == self.expected.splitlines())
            if kind == "count-matches":
                ok = text(source, self.source_text) and bool(target and target["regular"] and re.fullmatch(r"\s*3\s*", target.get("text") or ""))
        elif kind == "ssh-upload":
            source = observe("workstation", f"{lab}/{name}.txt")[0]
            target = observe("server", "/home/nixboy/delivery.txt")[0]
            ok = text(source, self.payload) and text(target, self.payload)
        elif kind == "ssh-download":
            source = observe("server", f"/home/nixboy/{name}.txt")[0]
            target = observe("workstation", lab + "/result.txt")[0]
            ok = text(source, self.payload) and text(target, self.payload)
        elif kind == "ssh-remote-move":
            source, target = observe("server", f"/home/nixboy/incoming/{name}.txt", f"/home/nixboy/archive/{name}.txt")
            ok = source is None and text(target, self.payload)
        elif kind in ("ssh-key-login", "ssh-alias"):
            target = name if kind == "ssh-alias" else "nixboy@server"
            nonce = hashlib.sha256(f"{self.attempt_id}:{self.generation}".encode()).hexdigest()[:24]
            argv = ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "ConnectionAttempts=1",
                    "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
                    "-o", "PreferredAuthentications=publickey", "-o", "StrictHostKeyChecking=yes", target,
                    "/usr/bin/printf", nonce]
            code, output, error = self._exec("workstation", argv)
            ok = code == 0 and output == nonce
        else:
            raise RuntimeError("missing validator")
        if ok:
            return True, "All exercise state checks passed."
        if kind in PERMISSIONS:
            observed = "missing or inaccessible" if not target else f"mode {target['mode']:04o}" if target["mode"] is not None else "inaccessible"
            feedback = f"The target is {observed}. Check owner/group/other access, ownership and preserved contents against ~/CHALLENGE.txt."
        elif kind in ("filter-lines", "sort-unique", "extract-field", "count-matches", "replace-text"):
            feedback = "The output does not match the requested transformation, or the source changed. Check ~/lab/result.txt, record order and line endings."
        elif kind in ("ssh-key-login", "ssh-alias"):
            feedback = "The required noninteractive public-key SSH connection did not succeed. Check the host alias, authorized key and .ssh file permissions."
        elif self.network:
            feedback = "The expected remote/local file state is incomplete. Check which machine owns each path, complete file contents and whether the source must remain."
        else:
            feedback = "The requested filesystem state is incomplete. Check exact source/target paths, preserved file contents and whether the old path must disappear."
        return False, feedback

    def _teardown(self):
        try:
            self._compose("down", "--volumes", "--remove-orphans", "--timeout", "3", timeout=15)
        finally:
            self.services = {}
            self.container_id = None

    def reset(self):
        self._teardown()
        return self.start()

    def close(self):
        if self._closed:
            return
        try:
            self._teardown()
        finally:
            self._closed = True
            self.client.close()


def Environment(client, spec, *args, **kwargs):
    """Keep ordinary tasks on their established container implementation."""
    from challenge_content import QEMU_KIND
    if spec.kind == QEMU_KIND:
        from qemu_env import QemuEnvironment
        return QemuEnvironment(client, spec, *args, **kwargs)
    return ComposeEnvironment(client, spec, *args, **kwargs)


def cleanup_environments(client, state_dir):
    cleanup_owned(client, state_dir)
    from qemu_env import cleanup_qemu_owned
    cleanup_qemu_owned(client, state_dir)
