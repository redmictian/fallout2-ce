import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qemu_env import QemuEnvironment, digest_artifacts, manifest, settings


class GuestValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = QemuEnvironment(types.SimpleNamespace(), types.SimpleNamespace(kind="qemu-bt05"),
                                   "test-attempt", 1, 0, self.directory.name)
        self.env.checkpoint = {"initial_boot_id": "11111111-1111-1111-1111-111111111111", "captured_at": 10}
        e = self.env.expected
        self.probe = {
            "boot_id": "22222222-2222-2222-2222-222222222222",
            "normal": {"stdout": "active"},
            "mount": {"stdout": json.dumps({"filesystems": [{"uuid": e["data_uuid"], "fstype": "ext4",
                         "target": e["mount"], "options": "rw,relatime"}]})},
            "fstab": "\n".join(e["fstab_unrelated"] + [f"UUID={e['data_uuid']} {e['mount']} ext4 defaults,x-systemd.device-timeout=60s 0 2"]) + "\n",
            "payload": {"sha256": e["payload_sha256"], "size": e["payload_bytes"], "uid": 0, "gid": 0,
                        "mode": 0o644, "mtime": 1768478400},
            "root_peer": {"sha256": e["root_peer_sha256"]},
            "service": {"ActiveState": "active", "UnitFileState": "enabled", "User": "nobody"},
            "worker": {"status": "Uid:\t65534\t65534\t65534\t65534\n", "cmdline": "/usr/bin/python3 -m http.server 8080"},
        }
        self.http = {"status": 200, "sha256": e["payload_sha256"], "bytes": e["payload_bytes"]}
        self.events = [{"event": "RESET", "data": {"guest": True}, "time": 11}]
        self.serial = "Linux version 6.12.113+deb13-cloud-amd64 new guest boot\n"

    def tearDown(self):
        self.directory.cleanup()

    def checks(self):
        return self.env._decide(self.probe, self.http, self.events, self.serial)

    def test_valid_outcome_requires_all_independent_components(self):
        self.assertTrue(all(self.checks().values()))
        self.http["sha256"] = "0" * 64
        self.assertFalse(self.checks()["outside_http"])

    def test_removed_mount_and_decoy_identical_http_are_rejected(self):
        self.probe["fstab"] = "\n".join(self.env.expected["fstab_unrelated"]) + "\n"
        self.assertFalse(self.checks()["mandatory_fstab"])
        self.probe["mount"]["stdout"] = json.dumps({"filesystems": [{"uuid": self.env.expected["decoy_uuid"],
            "fstype": "ext4", "target": self.env.expected["mount"], "options": "rw"}]})
        self.assertFalse(self.checks()["expected_filesystem"])
        self.assertTrue(self.checks()["outside_http"])

    def test_mount_policy_bypasses_and_malformed_fstab_do_not_pass(self):
        for policy in ("nofail", "noauto", "x-systemd.automount", "ro"):
            with self.subTest(policy=policy):
                altered = copy.deepcopy(self.probe)
                altered["fstab"] = altered["fstab"].replace("defaults,", policy + ",")
                self.assertFalse(self.env._decide(altered, self.http, self.events, self.serial)["mandatory_fstab"])
        self.probe["fstab"] = "'broken quote"
        self.assertFalse(self.checks()["mandatory_fstab"])

    def test_reboot_needs_changed_boot_id_external_event_and_new_kernel_text(self):
        for identity, events, serial in (
            (self.env.checkpoint["initial_boot_id"], self.events, self.serial),
            (self.probe["boot_id"], [], self.serial),
            (self.probe["boot_id"], self.events, "copied success marker"),
        ):
            altered = copy.deepcopy(self.probe)
            altered["boot_id"] = identity
            self.assertFalse(self.env._decide(altered, self.http, events, serial)["genuine_reboot"])

    def test_changed_protected_metadata_and_actual_root_worker_do_not_pass(self):
        self.probe["payload"]["mode"] = 0o666
        self.probe["worker"]["status"] = "Uid:\t0\t0\t0\t0\n"
        self.assertFalse(self.checks()["protected_metadata"])
        self.assertFalse(self.checks()["service"])

    def test_recovery_and_facility_failure_are_errors_never_incomplete(self):
        for state in ("recovery", "booting", "off", "failure"):
            with self.subTest(state=state), mock.patch.object(self.env, "status", return_value={"state": state, "feedback": state}):
                result = self.env.check()
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["facility_failure"], state == "failure")

    def test_old_reset_or_host_reset_is_not_reboot_evidence(self):
        values = [
            {"event": "RESET", "data": {"guest": True}, "time": 9},
            {"event": "RESET", "data": {"guest": False}, "time": 11},
            self.events[0],
        ]
        (self.env.evidence / "qmp-events.jsonl").write_text("\n".join(json.dumps(x) for x in values) + "\n{partial")
        self.assertEqual(self.env._reboot_events(), [self.events[0]])

    def test_catalog_identity_includes_pinned_artifact_and_operator_choices(self):
        first = digest_artifacts()
        with mock.patch.dict("os.environ", {"NIXBOY_QEMU_IMAGE": "example:changed"}):
            self.assertNotEqual(digest_artifacts(), first)
        with mock.patch.dict("os.environ", {"NIXBOY_QEMU_IMAGES_VOLUME": "../unsafe"}):
            with self.assertRaises(ValueError):
                settings()
        self.assertEqual(len(manifest()["hashes"]), 3)

    def test_resume_does_not_inject_geometry_commands_into_an_active_guest_job(self):
        self.env._console_sized = True
        with mock.patch.object(self.env, "_call") as call:
            self.env.set_console_size(35, 139)
            call.assert_not_called()
        self.env._console_sized = False
        with self.assertRaises(ValueError):
            self.env.set_console_size(0, 139)

    def test_recovery_setup_retries_enter_until_real_prompt_owns_the_tty(self):
        clock = [0]
        def advance(seconds):
            clock[0] += 3
        with mock.patch.object(self.env, "_serial", side_effect=[
                "emergency mode", "(or press Control-D to continue): ",
                "(or press Control-D to continue): ", "root@guest:~# "]), \
             mock.patch.object(self.env, "_call") as call, \
             mock.patch("qemu_env.time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch("qemu_env.time.sleep", side_effect=advance):
            self.env._open_recovery_prompt()
        self.assertEqual(call.call_count, 2)
        for args in call.call_args_list:
            self.assertEqual(args.args, ("serial-send", "\r"))


if __name__ == "__main__":
    unittest.main()
