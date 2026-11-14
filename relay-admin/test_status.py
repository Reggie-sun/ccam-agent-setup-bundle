import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_server as fixture

spec = importlib.util.spec_from_file_location('status_broker', Path(__file__).with_name('login_broker.py'))
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class ProbeStatusTests(unittest.TestCase):
    def test_automatic_health_has_only_safe_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'state.json'
            p.write_text(json.dumps({'profiles': {'a'*16: {
                'status': 'expired', 'checked_at': 100, 'last_success_at': None,
                'next_check_at': 400, 'token': 'private'}, 'b'*16: {'status': 'invented'}}}))
            self.assertEqual(broker.auth_health(p), {'a'*16: {
                'auth_status': 'expired', 'auth_checked_at': 100,
                'auth_last_success_at': None, 'auth_next_check_at': 400}})
            p.write_text('[]')
            self.assertEqual(broker.auth_health(p), {})
    def test_missing_or_bad_state_never_means_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'state.json'
            self.assertEqual(broker.probe_health(p), {})
            for value in ('bad json', '[]', '{"profiles": []}', '{"profiles": {}}'):
                p.write_text(value)
                self.assertEqual(broker.probe_health(p), {})

    def test_only_warning_codes_and_schedule_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'state.json'
            p.write_text(json.dumps({'profiles': {
                'a'*16: {'kind': 'auth', 'not_before': 12345, 'access_token': 'private'},
                'b'*16: {'kind': 'rate_limited', 'not_before': True},
                'c'*16: {'kind': 'failure', 'not_before': float('nan')},
                'd'*16: {'kind': 'ok', 'not_before': 12345},
            }}))
            self.assertEqual(broker.probe_health(p), {
                'a'*16: {'probe_status': 'auth', 'next_probe_at': 12345},
                'b'*16: {'probe_status': 'rate_limited', 'next_probe_at': None},
                'c'*16: {'probe_status': 'failure', 'next_probe_at': None},
            })


class PublicStatusTests(unittest.TestCase):
    def test_public_automatic_observation_is_forwarded_without_credentials(self):
        observation = {'auth_status':'expired', 'auth_checked_at':100, 'auth_next_check_at':400, 'token':'private'}
        with patch.object(self.module,'upstream',return_value={'profiles':[{'id':'a'*16}]}), patch.object(self.module,'login_request',return_value=(200,{'profiles':{'a'*16:observation}})):
            status, data = self.client.request()
        self.assertEqual(status, 200)
        self.assertEqual(data['profiles'][0]['auth_status'], 'expired')
        self.assertEqual(data['profiles'][0]['auth_checked_at'], 100)
        self.assertNotIn('token', data['profiles'][0])
    @classmethod
    def setUpClass(cls):
        fixture.SecurityTests.setUpClass()
        cls.client = fixture.SecurityTests()
        cls.module = cls.client.module

    @classmethod
    def tearDownClass(cls):
        fixture.SecurityTests.tearDownClass()

    def test_public_status_keeps_distinct_auth_and_probe_signals(self):
        p = {'id':'a'*16, 'state':'eligible', 'last_used_at':'2026-11-14T00:00:00Z', 'credential':'private'}
        warning = {'probe_status':'auth', 'next_probe_at':12345, 'credential':'private'}
        with patch.object(self.module,'upstream',return_value={'profiles':[p]}), patch.object(self.module,'login_request',return_value=(200,{'profiles':{'a'*16:warning}})):
            status,data=self.client.request()
        self.assertEqual(status,200)
        actual=data['profiles'][0]
        self.assertEqual(actual['state'],'eligible')
        self.assertEqual(actual['probe_status'],'auth')
        self.assertEqual(actual['last_used_at'],p['last_used_at'])
        self.assertNotIn('credential',actual)

    def test_missing_broker_keeps_list_but_verification_unknown(self):
        with patch.object(self.module,'upstream',return_value={'profiles':[{'id':'a'*16,'state':'eligible'}]}), patch.object(self.module,'login_request',side_effect=OSError()):
            status,data=self.client.request()
        self.assertEqual(status,200)
        self.assertEqual(data['profiles'][0]['probe_status'],'unknown')
        self.assertIsNone(data['profiles'][0]['next_probe_at'])

    def test_internal_status_is_not_public(self):
        self.assertEqual(self.client.request('/internal/probe-health')[0],404)


if __name__ == '__main__':
    unittest.main()
