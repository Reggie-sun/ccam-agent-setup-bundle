import http.client
import importlib.util
import json
import os
from pathlib import Path
import pty
import socket
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('login_broker', Path(__file__).with_name('login_broker.py'))
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)
OWNER = 'a' * 64
OTHER = 'b' * 64
AUTH_URL = ('https://claude.com/cai/oauth/authorize?response_type=code&client_id=test-client'
            '&code_challenge_method=S256&code_challenge=challenge&state=fixture-state'
            '&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback')


class FakeAccounts:
    def __init__(self, root):
        self.root = Path(root)
        self.uncertain_path = self.root / 'uncertain.json'
        self.commits = []
        self.discarded = []
        self.finished = []
        self.fail_commit = False
        self.mismatch = False
        self.processes = []
        self.resolve_ready = None
        self.resolve_release = None

    def target(self, _):
        return 'account-uuid'

    def prepare(self):
        return Path(tempfile.mkdtemp(dir=self.root))

    def spawn(self, directory):
        master, slave = pty.openpty()
        code = 'import sys; print(' + repr(AUTH_URL + '\nPaste code here >') + ',flush=True); sys.stdin.readline()'
        process = subprocess.Popen(['/usr/bin/python3', '-c', code], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        self.processes.append(process)
        return process, master

    def resolve(self, _):
        if self.resolve_ready:
            self.resolve_ready.set()
            self.resolve_release.wait(timeout=5)
        return 'credential-never-exposed'

    def commit(self, record, _):
        if self.mismatch:
            raise broker.LoginError(409, 'account_mismatch')
        record['commit_started'] = True
        self.commits.append(record['login_id'])
        if self.fail_commit:
            raise RuntimeError('secret-access-token-must-not-leak')
        return 'c' * 16

    def finish(self, directory):
        self.finished.append(directory)

    def discard(self, directory):
        self.discarded.append(directory)

    def uncertain(self, record):
        self.uncertain_path.write_text(json.dumps({key: record[key] for key in ('login_id', 'owner', 'expires_at')}))

    def clear_uncertain(self):
        self.uncertain_path.unlink(missing_ok=True)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.accounts = FakeAccounts(self.temp.name)
        self.now = time.time()
        self.manager = broker.Logins(self.accounts, clock=lambda: self.now)
        self.server = broker.Server(str(Path(self.temp.name) / 'test.sock'), broker.Handler)
        self.server.logins = self.manager
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        for record in self.manager.records.values():
            if record['status'] in broker.ACTIVE:
                self.manager.cancel(record['owner'], record['login_id'], {})
        if self.accounts.resolve_release:
            self.accounts.resolve_release.set()
        for process in self.accounts.processes:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                broker.Logins.stop(process)
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, method='GET', data=None, owner=OWNER):
        connection = UnixConnection(self.server.server_address)
        connection.request(method, path, body=json.dumps(data) if data is not None else None,
                           headers={'X-Login-Owner': owner, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def start(self, data=None):
        status, record = self.request('/api/logins', 'POST', data or {'mode': 'add'})
        self.assertEqual(status, 200)
        return record['login_id']

    def wait(self, login_id, status):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            result = self.manager.get(OWNER, login_id)
            if result['status'] == status:
                return result
            time.sleep(.02)
        self.fail('Expected state ' + status + ', got ' + str(result))

    def test_start_is_nonblocking_recovers_current_and_excludes_secrets(self):
        login_id = self.start()
        result = self.wait(login_id, 'waiting')
        self.assertEqual(result['authorization_url'], AUTH_URL)
        self.assertEqual(self.request('/api/logins/current')[1]['login_id'], login_id)
        self.assertEqual(self.request('/internal/status')[1], {'active': True})
        self.assertEqual(set(result), {'login_id', 'status', 'expires_at', 'authorization_url'})
        self.assertTrue(self.accounts.uncertain_path.exists())
        self.assertTrue(broker.Logins(self.accounts).active())

    def test_owner_is_required_and_cross_owner_cannot_observe_or_control(self):
        self.assertEqual(self.request('/api/logins/current', owner='')[0], 403)
        login_id = self.start()
        self.assertEqual(self.request('/api/logins/' + login_id, owner=OTHER)[0], 404)
        self.assertEqual(self.request('/api/logins/' + login_id + '/cancel', 'POST', {}, owner=OTHER)[0], 404)
        self.assertEqual(self.request('/api/logins/current', owner=OTHER)[1], {'status': 'idle'})

    def test_single_active_login_and_invalid_inputs(self):
        for data in ({'mode': 'other'}, {'mode': 'add', 'profile_id': 'c'*16}, {'mode': 'reauth'}, {'mode': 'reauth', 'profile_id': '../../x'}):
            self.assertEqual(self.request('/api/logins', 'POST', data)[0], 400)
        self.start()
        self.assertEqual(self.request('/api/logins', 'POST', {'mode': 'add'}, owner=OTHER)[0], 409)

    def test_submit_success_is_one_time_and_does_not_expose_credential(self):
        login_id = self.start()
        self.wait(login_id, 'waiting')
        self.assertEqual(self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})[0], 200)
        result = self.wait(login_id, 'succeeded')
        self.assertEqual(result['profile_id'], 'c'*16)
        self.assertNotIn('authorization_url', result)
        self.assertNotIn('credential', json.dumps(result))
        self.assertEqual(len(self.accounts.commits), 1)
        self.assertEqual(self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})[0], 409)

    def test_code_controls_and_multiline_are_refused(self):
        login_id = self.start()
        self.wait(login_id, 'waiting')
        for code in ('abc\nxyz123', '\x1b[31mfoo', 'https://example.com/callback', '', 'x'*2049):
            self.assertEqual(self.request('/api/logins/' + login_id + '/code', 'POST', {'code': code})[0], 400)
        self.assertFalse(self.accounts.commits)

    def test_cancel_kills_only_owned_process_and_cannot_import(self):
        login_id = self.start()
        self.wait(login_id, 'waiting')
        result = self.request('/api/logins/' + login_id + '/cancel', 'POST', {})[1]
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotIn('authorization_url', result)
        self.accounts.processes[0].wait(timeout=3)
        self.assertFalse(self.accounts.commits)
        end = time.monotonic() + 3
        while self.manager.active() and time.monotonic() < end:
            time.sleep(.02)
        self.assertFalse(self.manager.active())
        self.assertFalse(self.accounts.uncertain_path.exists())

    def test_uncertain_code_write_stops_process_without_import_then_allows_recovery(self):
        login_id = self.start()
        self.wait(login_id, 'waiting')
        with patch.object(broker.os, 'write', side_effect=OSError('fixture write failure')):
            result = self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})[1]
        self.assertEqual(result['status'], 'unknown')
        result = self.wait(login_id, 'failed')
        self.assertEqual(result['error_code'], 'code_submission_failed')
        self.assertFalse(self.accounts.commits)
        self.assertFalse(self.manager.active())

    def test_expired_session_cannot_import(self):
        login_id = self.start()
        self.wait(login_id, 'waiting')
        self.now += broker.TTL + 1
        self.wait(login_id, 'expired')
        self.assertEqual(self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})[0], 409)
        self.assertFalse(self.accounts.commits)

    def test_cancel_while_resolving_prevents_commit(self):
        self.accounts.resolve_ready, self.accounts.resolve_release = threading.Event(), threading.Event()
        login_id = self.start()
        self.wait(login_id, 'waiting')
        self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})
        self.assertTrue(self.accounts.resolve_ready.wait(timeout=3))
        self.request('/api/logins/' + login_id + '/cancel', 'POST', {})
        self.accounts.resolve_release.set()
        self.wait(login_id, 'cancelled')
        self.assertFalse(self.accounts.commits)

    def test_mismatch_is_failure_without_fallback_add(self):
        self.accounts.mismatch = True
        login_id = self.start({'mode': 'reauth', 'profile_id': 'c'*16})
        self.wait(login_id, 'waiting')
        self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})
        result = self.wait(login_id, 'failed')
        self.assertEqual(result['error_code'], 'account_mismatch')
        self.assertFalse(self.accounts.commits)

    def test_partial_persistence_is_unknown_blocks_retry_and_survives_restart(self):
        self.accounts.fail_commit = True
        login_id = self.start()
        self.wait(login_id, 'waiting')
        self.request('/api/logins/' + login_id + '/code', 'POST', {'code': 'fixture-code#state'})
        result = self.wait(login_id, 'unknown')
        self.assertNotIn('secret-access-token', json.dumps(result))
        self.assertEqual(self.request('/internal/status')[1], {'active': True})
        self.assertEqual(self.request('/api/logins', 'POST', {'mode': 'add'})[0], 409)
        restored = broker.Logins(self.accounts)
        self.assertTrue(restored.active())
        self.assertEqual(restored.current(OWNER)['status'], 'unknown')

    def test_route_allowlist(self):
        self.assertEqual(self.request('/api/export')[0], 404)
        self.assertEqual(self.request('/api/logins/' + 'a'*64 + '/credential', 'POST', {})[0], 404)


class ParserAndRepositoryTests(unittest.TestCase):
    def test_only_observed_official_url_and_pkce_shape_are_accepted(self):
        self.assertEqual(broker.authorization_url('\x1b]8;;'+AUTH_URL+'\x07'+AUTH_URL+'\x1b]8;;\x07'), AUTH_URL)
        for value in (AUTH_URL.replace('claude.com/', 'evil.example/'), AUTH_URL.replace('S256', 'plain'), AUTH_URL.replace('https://claude.com', 'https://claude.com.evil.example'), AUTH_URL.replace('oauth/authorize', 'other'), AUTH_URL + '&state=duplicate', AUTH_URL + '&code_verifier=must-not-leak'):
            self.assertIsNone(broker.authorization_url(value))

    def test_real_commit_requires_same_uuid_and_profile_and_preserves_refresh(self):
        adapter = broker.CuAccounts.__new__(broker.CuAccounts)
        target = SimpleNamespace(id='a'*16, kind='oauth', account_uuid='uuid-one', name='original')
        from unittest.mock import Mock
        adapter.config = SimpleNamespace(load_pool=Mock(return_value=SimpleNamespace(get=lambda _: target)))
        adapter.profiles = SimpleNamespace(find_by_account_uuid=Mock(return_value=target), upsert_oauth_profile=Mock(return_value=(target, True)))
        adapter.oauth = SimpleNamespace(plan_from_account=lambda _: 'pro')
        adapter.uncertain = Mock()
        record = {'mode': 'reauth', 'target_id':target.id, 'target_uuid':target.account_uuid, 'directory':Path('/fixture')}
        imported = SimpleNamespace(access_token='access-fixture', refresh_token='refresh-fixture', expires_at=123)
        account = SimpleNamespace(account_uuid='wrong-uuid', email='fixture@example.com')
        with self.assertRaises(broker.LoginError):
            adapter.commit(record, (imported, account))
        adapter.profiles.upsert_oauth_profile.assert_not_called()
        account.account_uuid = 'uuid-one'
        self.assertEqual(adapter.commit(record, (imported, account)), target.id)
        kwargs = adapter.profiles.upsert_oauth_profile.call_args.kwargs
        self.assertEqual(kwargs['refresh_token'], 'refresh-fixture')
        self.assertEqual(kwargs['expires_at'], 123)
        self.assertEqual(kwargs['name'], 'original')

    def test_marker_is_durable_before_upsert_and_survives_process_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import Mock
            adapter = broker.CuAccounts.__new__(broker.CuAccounts)
            adapter.uncertain_path = Path(directory) / 'uncertain.json'
            adapter.oauth = SimpleNamespace(plan_from_account=lambda _: 'pro')
            record = {'mode': 'add', 'directory': Path(directory) / 'web-login-fixture',
                      'login_id': 'a'*64, 'owner': OWNER, 'expires_at': 123}
            def crash(**_):
                self.assertTrue(adapter.uncertain_path.exists())
                self.assertTrue(broker.Logins(adapter).active())
                raise SystemExit('simulated process death')
            adapter.profiles = SimpleNamespace(upsert_oauth_profile=crash)
            imported = SimpleNamespace(access_token='access-fixture', refresh_token='refresh-fixture', expires_at=123)
            account = SimpleNamespace(account_uuid='uuid', email='fixture@example.com')
            with patch.object(broker.os, 'fsync', wraps=broker.os.fsync) as fsync:
                with self.assertRaises(SystemExit):
                    adapter.commit(record, (imported, account))
                self.assertEqual(fsync.call_count, 2)
            restored = broker.Logins(adapter)
            self.assertEqual(restored.current(OWNER)['status'], 'unknown')
            self.assertNotIn('access-fixture', adapter.uncertain_path.read_text())
            adapter.clear_uncertain()
            self.assertFalse(adapter.uncertain_path.exists())

    def test_plaintext_cleanup_failure_is_not_silently_accepted(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '.credentials.json').write_text('fixture')
            adapter = broker.CuAccounts.__new__(broker.CuAccounts)
            adapter.cli = SimpleNamespace(_drop_plaintext_login=Mock(), _discard_login_dir=Mock())
            with self.assertRaises(OSError):
                adapter.finish(path)
            with self.assertRaises(OSError):
                adapter.discard(path)


if __name__ == '__main__':
    unittest.main()
