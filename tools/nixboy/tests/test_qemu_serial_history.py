import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'container/qemu/serial_history.py'
spec = importlib.util.spec_from_file_location('qemu_serial_history', path)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class SerialReplayTests(unittest.TestCase):
    def test_replayed_getty_queries_do_not_enter_a_new_shell_as_replies(self):
        raw = b'Linux version kernel\r\n\x1b[999C\x1b[6n\x1b[?6n\x1b[c\x1b[>0croot:~# '
        result = history.replay_bytes(raw)
        self.assertEqual(result, b'Linux version kernel\r\n\x1b[999Croot:~# ')
        self.assertIn(b'\x1b[6n', raw) # Raw evidence was not mutated.

    def test_replay_preserves_real_failure_output_colors_and_prompt(self):
        raw = b'\x1b[31m[FAILED]\x1b[0m mandatory mount\r\nemergency mode\r\nroot:~# '
        self.assertEqual(history.replay_bytes(raw), raw)


if __name__ == '__main__':
    unittest.main()
