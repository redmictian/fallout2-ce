"""The serial attachment cannot select an outer shell/control identity."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


class QemuAttachmentTests(unittest.TestCase):
    def test_guest_console_uses_raw_relay_and_same_identity_for_detach(self):
        client = mock.Mock()
        client.api.exec_create.return_value = {'Id': 'relay'}
        client.api.exec_start.return_value = b''
        client.api.exec_inspect.return_value = {'ExitCode': 0}
        with mock.patch.object(bridge, 'Duplex'), mock.patch.object(bridge.threading.Thread, 'start'):
            shell = bridge.TrainingShell(client, 'owned-runner', lambda *a: None, lambda: None,
                attachment={'kind': 'qemu-serial', 'user': '1001:1001',
                            'argv': ['python3', '/opt/nixboy-qemu/control.py', 'console']})
            shell.start(35, 139, {})
            create = client.api.exec_create.call_args
            self.assertEqual(create.kwargs['user'], '1001:1001')
            program = create.args[1][3]
            self.assertIn('tty.setraw(0)', program)
            self.assertIn("'/opt/nixboy-qemu/control.py','console'", program)
            self.assertNotIn('/opt/nixboy/shell.py', program)
            self.assertNotIn('qemu-system', program)
            shell.close()
            stop = client.api.exec_create.call_args
            self.assertEqual(stop.kwargs['user'], '1001:1001')
            self.assertEqual(stop.args[1][-1], shell.attachment_token)
            self.assertIn('/tmp/nixboy-console', stop.args[1][3])
            client.api.remove_container.assert_not_called()

    def test_arbitrary_program_or_private_controller_identity_rejected(self):
        for attachment in ({'kind': 'qemu-serial', 'user': '1000:1000', 'argv': ['/bin/bash']},
                           {'kind': 'qemu-serial', 'user': '1001:1001', 'argv': ['/bin/bash']}):
            with self.subTest(attachment=attachment), self.assertRaises(ValueError):
                bridge.TrainingShell(None, 'owned', None, None, attachment=attachment)


if __name__ == '__main__':
    unittest.main()
