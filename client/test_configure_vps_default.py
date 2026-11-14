import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('client_setup', Path(__file__).with_name('configure_vps_default.py'))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home/'input.json'
        self.values = {'ANTHROPIC_BASE_URL':'https://relay.example.com', 'ANTHROPIC_AUTH_TOKEN':'fixture-token', 'ANTHROPIC_CUSTOM_HEADERS':''}
        self.config.write_text(json.dumps(self.values))

    def test_merge_preserves_settings_accounts_and_backup(self):
        path = self.home/'.local/share/ccam/profiles/example/settings.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'theme':'dark','env':{'UNRELATED':'keep','ANTHROPIC_API_KEY':'old'}}))
        original = path.read_bytes()
        backup = setup.configure(self.home, self.config)
        data = json.loads(path.read_text())
        self.assertEqual(data['theme'], 'dark')
        self.assertEqual(data['env']['UNRELATED'], 'keep')
        self.assertNotIn('ANTHROPIC_API_KEY', data['env'])
        self.assertEqual(data['env']['ANTHROPIC_BASE_URL'], self.values['ANTHROPIC_BASE_URL'])
        self.assertEqual((backup/path.relative_to(self.home)).read_bytes(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(setup.configure(self.home, self.config))

    def test_launcher_overrides_old_function_without_deleting_it(self):
        binary = self.home/'official-claude'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o700)
        shell = self.home/'.bashrc'
        shell.write_text('claude() { old-client "$@"; }\n')
        setup.configure(self.home, self.config, binary)
        self.assertIn('old-client', shell.read_text())
        self.assertIn(setup.START, shell.read_text())
        self.assertIsNone(setup.configure(self.home, self.config, binary))
        self.assertEqual(shell.read_text().count(setup.START), 1)
        self.assertNotIn('--dangerously-skip-permissions', shell.read_text())
        self.assertEqual((self.home/'.local/bin/claude').stat().st_mode & 0o777, 0o700)

    def test_invalid_url_does_not_write(self):
        self.values['ANTHROPIC_BASE_URL']='http://relay.example.com'
        self.config.write_text(json.dumps(self.values))
        with self.assertRaises(ValueError):
            setup.configure(self.home, self.config)
        self.assertFalse((self.home/'.claude').exists())

    def test_symlink_target_is_not_overwritten(self):
        target = self.home/'keep.json'
        target.write_text('{}')
        path = self.home/'.claude/settings.json'
        path.parent.mkdir()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            setup.configure(self.home, self.config)
        self.assertEqual(target.read_text(), '{}')

    def test_chmod_failure_preserves_existing_file(self):
        path = self.home/'.claude/settings.json'
        path.parent.mkdir()
        path.write_text('{"theme":"keep"}')
        original_chmod = setup.os.chmod
        def fail_temporary(target, *args, **kwargs):
            if Path(target).name.startswith('.claude-vps-'):
                raise OSError('fixture')
            return original_chmod(target, *args, **kwargs)
        with patch.object(setup.os, 'chmod', side_effect=fail_temporary):
            with self.assertRaisesRegex(setup.ConfigurationWriteError, 'Private backup:'):
                setup.configure(self.home, self.config)
        self.assertEqual(path.read_text(), '{"theme":"keep"}')
        self.assertFalse(list(path.parent.glob('.claude-vps-*')))

    def test_later_write_failure_rolls_back_prior_updates(self):
        first = self.home/'.claude/settings.json'
        second = self.home/'.local/share/ccam/profiles/example/settings.json'
        for path in (first, second):
            path.parent.mkdir(parents=True)
            path.write_text('{"theme":"keep"}')
        original_replace = setup.os.replace
        calls = 0
        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('fixture disk error')
            return original_replace(source, target)
        with patch.object(setup.os, 'replace', side_effect=fail_second):
            with self.assertRaisesRegex(setup.ConfigurationWriteError, 'Updated files rolled back'):
                setup.configure(self.home, self.config)
        for path in (first, second):
            self.assertEqual(path.read_text(), '{"theme":"keep"}')
        self.assertEqual(calls, 3)


if __name__ == '__main__':
    unittest.main()
