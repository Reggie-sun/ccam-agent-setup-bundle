import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import install_cu_patch as installer

DIFF = installer.PATCH_FILE.read_text()


def sides(prefix):
    """A synthetic daemon built from the real patch's hunks: (original, patched)."""
    old, new = [], []
    for hunk in DIFF.split('\n@@ ')[1:]:
        lines = hunk.splitlines(keepends=True)[1:]
        old.append(''.join(line[1:] for line in lines if line[0] in ' -'))
        new.append(''.join(line[1:] for line in lines if line[0] in ' +'))
    # Kept inside a string literal so the installer's compile() check passes on the synthetic file.
    wrap = lambda parts: (prefix + "TEXT = r'''\n" + '\n# filler\n'.join(parts) + "'''\n").encode()
    return wrap(old), wrap(new)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class CuPatchTests(unittest.TestCase):
    def setUp(self):
        # Two accepted baselines, like cu 1.4.0 and cu 1.4.0 + auth monitor.
        self.plain, self.plain_after = sides('# plain\n')
        self.monitor, self.monitor_after = sides('# with auth monitor\n')
        sources = {sha(self.plain): sha(self.plain_after), sha(self.monitor): sha(self.monitor_after)}
        self.sources = patch.object(installer, 'SOURCES', sources)
        self.sources.start()
        self.addCleanup(self.sources.stop)

    def test_real_patch_adds_quota_age_and_failure_log(self):
        self.assertIn('item["usage_observed_at"] = observed_at.get(item["id"])', DIFF)
        self.assertIn('request_failures.jsonl', DIFF)

    def test_unsupported_daemon_is_refused_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'daemon.py').write_bytes(self.plain + b'\n# local edit\n')
            with self.assertRaises(ValueError):
                installer.install(root)
            self.assertEqual((root / 'daemon.py').read_bytes(), self.plain + b'\n# local edit\n')
            self.assertEqual([p.name for p in root.iterdir()], ['daemon.py'])

    def test_wrong_result_hash_is_refused(self):
        with patch.object(installer, 'SOURCES', {sha(self.plain): '0' * 64}):
            with self.assertRaises(ValueError):
                installer.patch_daemon(self.plain, DIFF)

    def test_both_baselines_patch_backup_idempotence_and_rollback(self):
        for original, expected in ((self.plain, self.plain_after), (self.monitor, self.monitor_after)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'daemon.py').write_bytes(original)
                backup = installer.install(root)
                self.assertEqual((root / 'daemon.py').read_bytes(), expected)
                self.assertEqual((backup / 'daemon.py').read_bytes(), original)
                self.assertIsNone(installer.install(root))
                installer.rollback(root, backup)
                self.assertEqual((root / 'daemon.py').read_bytes(), original)

    def test_rollback_refuses_a_daemon_changed_after_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'daemon.py').write_bytes(self.plain)
            backup = installer.install(root)
            (root / 'daemon.py').write_bytes(self.plain_after + b'\n# later edit\n')
            with self.assertRaises(ValueError):
                installer.rollback(root, backup)
            self.assertTrue((root / 'daemon.py').read_bytes().endswith(b'# later edit\n'))

    def test_failed_write_leaves_original_and_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'daemon.py').write_bytes(self.plain)
            with patch.object(installer.os, 'fsync', side_effect=OSError('disk full')), self.assertRaises(OSError):
                installer.install(root)
            self.assertEqual((root / 'daemon.py').read_bytes(), self.plain)
            self.assertEqual([p.name for p in root.iterdir() if p.name.startswith('.daemon.py.')], [])

    def test_install_and_rollback_replace_atomically_keeping_mode_and_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daemon = root / 'daemon.py'
            daemon.write_bytes(self.plain)
            daemon.chmod(0o640)
            before = daemon.stat()
            with patch.object(installer.os, 'replace', wraps=installer.os.replace) as replace:
                backup = installer.install(root)
                installer.rollback(root, backup)
            self.assertEqual([call.args[1] for call in replace.call_args_list], [daemon, daemon])
            after = daemon.stat()
            self.assertEqual((after.st_mode & 0o777, after.st_uid, after.st_gid), (0o640, before.st_uid, before.st_gid))
            self.assertEqual(daemon.read_bytes(), self.plain)
            self.assertEqual(sorted(p.name for p in root.iterdir() if p.is_file()), ['daemon.py'])

    def test_friendly_errors_for_missing_cu_or_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'daemon.py').write_bytes(self.plain_after)
            with self.assertRaisesRegex(ValueError, 'No daemon.py'):
                installer.rollback(root, root / 'missing')
            spec = type('Spec', (), {'origin': str(root / '__init__.py')})()
            with patch.object(installer, 'version', return_value='1.4.0'), \
                    patch.object(installer.importlib.util, 'find_spec', return_value=spec), \
                    patch('sys.argv', ['install_cu_patch.py', '--rollback', str(root / 'missing')]), \
                    self.assertRaisesRegex(SystemExit, 'No daemon.py'):
                installer.main()
        with patch.object(installer, 'version', side_effect=installer.PackageNotFoundError('claude-unlimited')), \
                patch('sys.argv', ['install_cu_patch.py']), self.assertRaisesRegex(SystemExit, 'not found'):
            installer.main()

if __name__ == '__main__':
    unittest.main()
