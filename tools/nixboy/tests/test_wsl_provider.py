from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.name != 'nt':
    import wsl_provider


@unittest.skipIf(os.name == 'nt', 'Linux provider process identities')
class ProviderOwnershipTests(unittest.TestCase):
    def test_reused_process_id_is_not_an_owned_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'provider.json'
            path.write_text(json.dumps({'format': 'nixboy-wsl-provider-v1',
                                       'provider_pid': os.getpid(), 'provider_birth': 'stale'}))
            self.assertIsNone(wsl_provider.owned(path))

    def test_live_unrelated_process_cannot_be_signaled_as_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'provider.json'
            path.write_text(json.dumps({'format': 'nixboy-wsl-provider-v1',
                'provider_pid': os.getpid(), 'provider_birth': wsl_provider.birth(os.getpid())}))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                wsl_provider.owned(path)


if __name__ == '__main__':
    unittest.main()
