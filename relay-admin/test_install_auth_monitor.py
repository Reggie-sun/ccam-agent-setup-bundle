import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import install_auth_monitor as installer


class InstallerTests(unittest.TestCase):
    def test_unsupported_daemon_is_not_changed(self):
        with self.assertRaises(ValueError):
            installer.patch_daemon(b'unknown daemon')

    def test_patch_backup_and_idempotence(self):
        original = ('_OAUTH_REFRESH_LOOP_INTERVAL_SECONDS = 60\n'
                    'def loop():\n    while True:\n        try:\n'+installer.OLD+
                    '():\n    pass\n').encode()
        with tempfile.TemporaryDirectory() as tmp, patch.object(installer, 'BASE_SHA256', hashlib.sha256(original).hexdigest()):
            root = Path(tmp)
            (root/'daemon.py').write_bytes(original)
            module = root/'source.py'
            module.write_text('def tick(gateway):\n    pass\n')
            backup = installer.install(root, module)
            self.assertEqual((backup/'daemon.py').read_bytes(), original)
            self.assertIn(installer.NEW.encode(), (root/'daemon.py').read_bytes())
            self.assertIsNone(installer.install(root, module))
            (root/'daemon.py').write_bytes((root/'daemon.py').read_bytes()+b'\n# unrelated edit\n')
            with self.assertRaises(ValueError):
                installer.install(root, module)


if __name__ == '__main__':
    unittest.main()
