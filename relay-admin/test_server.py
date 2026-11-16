import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        config = Path(cls.temp.name) / 'auth.json'
        salt = b'fixture-only-salt'
        config.write_text(json.dumps({'username': 'admin', 'salt': salt.hex(), 'hash': hashlib.scrypt(b'test-password', salt=salt, n=16384, r=8, p=1).hex()}))
        with patch.dict(os.environ, {'ADMIN_AUTH_FILE': str(config)}):
            spec = importlib.util.spec_from_file_location('admin_server', Path(__file__).with_name('server.py'))
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.module.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path='/api/profiles', method='GET', data=None, auth=True, csrf=True, origin=None, username='admin', owner=None):
        headers = {'Content-Type': 'application/json'}
        if owner:
            headers['Cookie'] = '__Host-relay_login=' + owner
        if auth:
            headers['Authorization'] = 'Basic ' + base64.b64encode((username + ':test-password').encode()).decode()
        if csrf:
            headers['X-CSRF-Token'] = self.module.CSRF
        headers['Origin'] = origin or self.module.ORIGIN
        request = Request(self.url + path, method=method, headers=headers, data=json.dumps(data).encode() if data is not None else None)
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_auth_required(self):
        with patch.object(self.module, 'upstream') as gateway:
            self.assertEqual(self.request(auth=False)[0], 401)
            gateway.assert_not_called()

    def test_unicode_username_is_rejected(self):
        self.assertEqual(self.request(username='管理')[0], 401)

    def setUp(self):
        self.module.AUTH_CACHE.clear()

    def test_verified_header_skips_scrypt_until_expiry(self):
        with patch.object(self.module, 'upstream', return_value={'profiles': []}):
            self.assertEqual(self.request()[0], 200)
            self.module.AUTH_SLOTS.acquire()
            self.module.AUTH_SLOTS.acquire()
            try:
                with patch.object(self.module.hashlib, 'scrypt') as kdf:
                    self.assertEqual(self.request()[0], 200)
                    kdf.assert_not_called()
                    self.assertEqual(self.request(username='other')[0], 503)
                    kdf.assert_not_called()
                    for key in self.module.AUTH_CACHE:
                        self.module.AUTH_CACHE[key] = 0
                    self.assertEqual(self.request()[0], 503)
            finally:
                self.module.AUTH_SLOTS.release()
                self.module.AUTH_SLOTS.release()

    def test_failed_auth_is_not_cached(self):
        headers = {'Authorization': 'Basic ' + base64.b64encode(b'admin:wrong-password').decode()}
        for _ in range(2):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url + '/api/profiles', headers=headers))
            self.assertEqual(error.exception.code, 401)
        self.assertEqual(self.module.AUTH_CACHE, {})

    def test_auth_capacity_rejects_before_hashing(self):
        self.module.AUTH_SLOTS.acquire()
        self.module.AUTH_SLOTS.acquire()
        try:
            with patch.object(self.module.hashlib, 'scrypt') as kdf:
                self.assertEqual(self.request()[0], 503)
                kdf.assert_not_called()
        finally:
            self.module.AUTH_SLOTS.release()
            self.module.AUTH_SLOTS.release()

    def test_profile_response_does_not_expose_unlisted_fields(self):
        with patch.object(self.module, 'upstream', return_value={'profiles': [{'id': 'a'*16, 'name': '<script>', 'credential': 'secret', 'usage_5h_percent': None, 'usage_observed_at': 1794700000.5}]}):
            status, data = self.request()
            self.assertEqual(status, 200)
            self.assertNotIn('credential', data['profiles'][0])
            self.assertIsNone(data['profiles'][0]['usage_5h_percent'])
            self.assertEqual(data['profiles'][0]['usage_observed_at'], 1794700000.5)

    def test_unpatched_cu_omits_quota_age_but_patched_null_is_kept(self):
        rows = [{'id': 'a'*16, 'name': 'x'}, {'id': 'b'*16, 'name': 'y', 'usage_observed_at': None}]
        with patch.object(self.module, 'upstream', return_value={'profiles': rows}):
            status, data = self.request()
        self.assertEqual(status, 200)
        self.assertNotIn('usage_observed_at', data['profiles'][0])
        self.assertIn('usage_observed_at', data['profiles'][1])
        self.assertIsNone(data['profiles'][1]['usage_observed_at'])

    def test_profiles_include_validated_rotation_pointer(self):
        responses = {'/api/profiles': {'profiles': [{'id': 'a'*16, 'name': 'x'}]},
                     '/api/status': {'current_profile_id': 'a'*16, 'csrf_token': 'upstream-secret'},
                     '/api/settings': {'settings': {}}}
        with patch.object(self.module, 'upstream', side_effect=lambda path, *a, **k: responses[path]):
            status, data = self.request()
        self.assertEqual((status, data['current_profile_id']), (200, 'a'*16))
        self.assertNotIn('upstream-secret', json.dumps(data))
        responses['/api/status'] = {'current_profile_id': '<script>'}
        with patch.object(self.module, 'upstream', side_effect=lambda path, *a, **k: responses[path]):
            self.assertIsNone(self.request()[1]['current_profile_id'])
        def failing(path, *a, **k):
            if path == '/api/status':
                raise self.module.URLError('down')
            return responses[path]
        with patch.object(self.module, 'upstream', side_effect=failing):
            status, data = self.request()
        self.assertEqual((status, data['current_profile_id']), (200, None))
        import http.client
        for error in (http.client.RemoteDisconnected('gone'), ConnectionResetError(), http.client.IncompleteRead(b'')):
            def broken(path, *a, error=error, **k):
                if path == '/api/status':
                    raise error
                return responses[path]
            with patch.object(self.module, 'upstream', side_effect=broken):
                status, data = self.request()
            self.assertEqual((status, data['current_profile_id']), (200, None))
        for pointer in (12345, ['a'*16]):
            responses['/api/status'] = {'current_profile_id': pointer}
            with patch.object(self.module, 'upstream', side_effect=lambda path, *a, **k: responses[path]):
                self.assertIsNone(self.request()[1]['current_profile_id'])
        responses['/api/status'] = ['not', 'a', 'dict']
        with patch.object(self.module, 'upstream', side_effect=lambda path, *a, **k: responses[path]):
            self.assertEqual(self.request()[0], 200)

    def test_profile_list_transport_reset_is_502(self):
        import http.client
        with patch.object(self.module, 'upstream', side_effect=http.client.RemoteDisconnected('gone')):
            self.assertEqual(self.request()[0], 502)

    def test_alias_file_matches_email_case_insensitively(self):
        path = Path(self.temp.name) / 'aliases.json'
        upstream = {'profiles': [{'id': 'a'*16, 'name': 'User@Example.com'}, {'id': 'b'*16, 'name': 'other@example.com'}, {'id': 'c'*16, 'name': 'bad@example.com'}]}
        path.write_text(json.dumps({'user@example.com': 'work3', 'bad@example.com': '<script>'}))
        with patch.object(self.module, 'ALIAS_FILE', path), patch.object(self.module, 'upstream', return_value=upstream):
            data = self.request()[1]
        self.assertEqual([p['alias'] for p in data['profiles']], ['work3', None, None])
        for content in ('not json', '[1, 2]'):
            path.write_text(content)
            with patch.object(self.module, 'ALIAS_FILE', path), patch.object(self.module, 'upstream', return_value=upstream):
                status, data = self.request()
            self.assertEqual((status, [p['alias'] for p in data['profiles']]), (200, [None, None, None]))
        with patch.object(self.module, 'ALIAS_FILE', Path(self.temp.name) / 'missing.json'), patch.object(self.module, 'upstream', return_value=upstream):
            status, data = self.request()
        self.assertEqual((status, [p['alias'] for p in data['profiles']]), (200, [None, None, None]))

    def test_write_requires_origin_and_csrf(self):
        with patch.object(self.module, 'upstream') as gateway:
            self.assertEqual(self.request('/api/profiles/'+'a'*16, 'PATCH', {'enabled': False}, csrf=False)[0], 403)
            self.assertEqual(self.request('/api/profiles/'+'a'*16, 'PATCH', {'enabled': False}, origin='https://evil.example')[0], 403)
            gateway.assert_not_called()

    def test_api_allowlist(self):
        with patch.object(self.module, 'upstream') as gateway:
            self.assertEqual(self.request('/api/export')[0], 404)
            self.assertEqual(self.request('/api/profiles/'+'a'*16+'/credential', 'PATCH', {'enabled': False})[0], 404)
            gateway.assert_not_called()

    def test_invalid_changes_never_reach_cu(self):
        with patch.object(self.module, 'upstream') as gateway:
            for change in ({'priority': True}, {'priority': 0}, {'priority': 1000}, {'credential': 'secret'}, {'enabled': 'false'}, {'enabled': True, 'priority': 2}):
                self.assertEqual(self.request('/api/profiles/'+'a'*16, 'PATCH', change)[0], 400)
            gateway.assert_not_called()

    def test_valid_change_uses_upstream_csrf(self):
        with patch.object(self.module, 'login_request', return_value=(200, {'active': False})), patch.object(self.module, 'upstream', side_effect=[{'csrf_token': 'upstream-token'}, {}]) as gateway:
            self.assertEqual(self.request('/api/profiles/'+'a'*16, 'PATCH', {'enabled': False})[0], 200)
            self.assertEqual(gateway.call_args.args, ('/api/profiles/'+'a'*16, 'PATCH', {'enabled': False}, 'upstream-token'))

    def test_distribute_toggle_only_sets_that_cu_setting(self):
        with patch.object(self.module, 'login_request', return_value=(200, {'active': False})), patch.object(self.module, 'upstream', side_effect=[{'csrf_token': 'upstream-token'}, {}]) as gateway:
            self.assertEqual(self.request('/api/settings/distribute', 'PATCH', {'enabled': True})[0], 200)
            self.assertEqual(gateway.call_args.args, ('/api/settings', 'PATCH', {'distribute_sessions_default': True}, 'upstream-token'))
        with patch.object(self.module, 'upstream') as gateway:
            for body in ({'enabled': 'yes'}, {'priority': 3}, {'enabled': True, 'x': 1}, {'model_parity': {}}):
                self.assertEqual(self.request('/api/settings/distribute', 'PATCH', body)[0], 400)
            self.assertEqual(self.request('/api/settings', 'PATCH', {'distribute_sessions_default': True})[0], 404)
            self.assertEqual(self.request('/api/settings/distribute', 'PATCH', {'enabled': True}, csrf=False)[0], 403)
            self.assertEqual(self.request('/api/settings/distribute', 'PATCH', {'enabled': True}, origin='https://evil.test')[0], 403)
            gateway.assert_not_called()

    def test_profiles_report_distribute_mode(self):
        responses = {'/api/profiles': {'profiles': []}, '/api/status': {}, '/api/settings': {'settings': {'distribute_sessions_default': True, 'secret': 'x'}}}
        with patch.object(self.module, 'upstream', side_effect=lambda path, *a, **k: responses[path]):
            data = self.request()[1]
        self.assertIs(data['distribute'], True)
        self.assertNotIn('secret', json.dumps(data))
        def no_settings(path, *a, **k):
            if path == '/api/settings':
                raise OSError('down')
            return responses[path]
        with patch.object(self.module, 'upstream', side_effect=no_settings):
            status, data = self.request()
        self.assertEqual((status, data['distribute']), (200, None))
        with patch.object(self.module, 'login_request', return_value=(200, {'active': True})), patch.object(self.module, 'upstream') as gateway:
            self.assertEqual(self.request('/api/settings/distribute', 'PATCH', {'enabled': True})[0], 409)
            gateway.assert_not_called()
        responses['/api/settings'] = {'settings': {'distribute_sessions_default': 'true'}}
        with patch.object(self.module, 'upstream', side_effect=lambda path, *a, **k: responses[path]):
            self.assertIsNone(self.request()[1]['distribute'])

    def test_login_requires_authenticated_origin_csrf_and_owner(self):
        with patch.object(self.module, 'login_request') as gateway:
            for kwargs, expected in [({'auth': False}, 401), ({'csrf': False}, 403), ({'origin': 'https://evil.example'}, 403), ({}, 409)]:
                self.assertEqual(self.request('/api/logins', 'POST', {'mode': 'add'}, **kwargs)[0], expected)
            gateway.assert_not_called()

    def test_login_input_validation(self):
        with patch.object(self.module, 'login_request') as gateway:
            for data in ({'mode': 'add', 'command': 'sh'}, {'mode': 'reauth'}, {'mode': 'reauth', 'profile_id': '../secret'}, []):
                self.assertEqual(self.request('/api/logins', 'POST', data, owner='a'*64)[0], 400)
            for code in ('', 'a\ncommand', 'x'*4097):
                self.assertEqual(self.request('/api/logins/'+'b'*64+'/code', 'POST', {'code': code}, owner='a'*64)[0], 400)
            gateway.assert_not_called()

    def test_login_paths_and_owner_forwarding(self):
        with patch.object(self.module, 'login_request', return_value=(200, {'status': 'starting', 'login_id': 'b'*64})) as gateway:
            self.assertEqual(self.request('/api/logins', 'POST', {'mode': 'add'}, owner='a'*64)[0], 200)
            self.assertEqual(gateway.call_args.args, ('/api/logins', 'POST', 'a'*64, {'mode': 'add'}))
            self.assertEqual(self.request('/api/logins/current', owner='a'*64)[0], 200)
            self.assertEqual(self.request('/api/logins/'+'b'*64, owner='c'*64)[0], 200)
            self.assertEqual(gateway.call_args.args[2], 'c'*64)
            gateway.reset_mock()
            self.assertEqual(self.request('/internal/status', owner='a'*64)[0], 404)
            self.assertEqual(self.request('/api/logins/'+'b'*64+'/token', owner='a'*64)[0], 404)
            gateway.assert_not_called()

    def test_login_transport_failure_is_unconfirmed(self):
        with patch.object(self.module, 'login_request', side_effect=TimeoutError()):
            status, data = self.request('/api/logins', 'POST', {'mode': 'add'}, owner='a'*64)
            self.assertEqual(status, 502)
            self.assertEqual(data['error'], 'login_operation_unconfirmed')

    def test_active_login_blocks_profile_patch(self):
        with patch.object(self.module, 'login_request', return_value=(200, {'active': True})), patch.object(self.module, 'upstream') as gateway:
            self.assertEqual(self.request('/api/profiles/'+'a'*16, 'PATCH', {'enabled': False})[0], 409)
            gateway.assert_not_called()

    def test_broker_unavailable_blocks_profile_patch(self):
        with patch.object(self.module, 'login_request', side_effect=OSError()), patch.object(self.module, 'upstream') as gateway:
            self.assertEqual(self.request('/api/profiles/'+'a'*16, 'PATCH', {'enabled': False})[0], 503)
            gateway.assert_not_called()

    def test_broker_response_filters_credentials(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({'status': 'waiting', 'login_id': 'b'*64, 'access_token': 'private', 'refresh_token': 'private', 'output': 'raw'}).encode()
        with patch.object(self.module, 'LoginConnection', return_value=connection):
            status, data = self.module.login_request('/api/logins', 'POST', 'a'*64, {'mode': 'add'})
            self.assertEqual(data, {'status': 'waiting', 'login_id': 'b'*64})
            connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
