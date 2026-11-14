import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

# Use the real cu dataclasses/credential codec without contacting any service.
if os.environ.get("CU_SOURCE"):
    sys.path.insert(0, os.environ["CU_SOURCE"])
try:
    import claude_unlimited
except ImportError:
    raise unittest.SkipTest("Install cu 1.4.0 or set CU_SOURCE to run monitor tests")
spec = importlib.util.spec_from_file_location(
    "claude_unlimited.auth_monitor", Path(__file__).with_name("cu_auth_monitor.py"))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
from claude_unlimited.router import ProfileRuntime, ProfileState


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.now = 10000.0
        self.profile = monitor.config.Profile("one", "not-persisted@example.com", account_uuid="uuid-one")
        self.pool = monitor.config.Pool(profiles=[self.profile])
        self.tokens = {"one": "secret-old"}
        self.gateway = SimpleNamespace(
            _lock=threading.Lock(), _runtime={"one": ProfileRuntime("one", 1, 98, True)},
            _maybe_refresh_credential=Mock(side_effect=lambda p, stored: stored),
            _observe=Mock(), _persist=Mock(), _queue_recovered_usage_read=Mock(),
            run_credential_checks_now=Mock())
        self.start_patch("time.time", side_effect=lambda: self.now)
        self.start_patch("config.load_pool", side_effect=lambda: self.pool)
        self.start_patch("secret_store.get_token", side_effect=lambda pid: self.tokens.get(pid))
        self.request = self.start_patch("_opener.open", side_effect=AssertionError("real network forbidden"))
        self.m = monitor.Monitor(self.path)
        self.probe = Mock(return_value=(200, {}, {"account": {"uuid": "uuid-one"}}))
        self.m._request = self.probe

    def start_patch(self, name, **kwargs):
        # Module loaded from a staging path; resolve dotted members directly.
        target = monitor
        parts = name.split(".")
        for part in parts[:-1]:
            target = getattr(target, part)
        patcher = patch.object(target, parts[-1], **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def tick(self):
        self.m.tick(self.gateway)
        return self.m.profiles["one"]

    def test_valid_schedule_last_success_and_sanitized_atomic_file(self):
        state = self.tick()
        self.assertEqual(state, dict(status="valid", checked_at=10000, last_success_at=10000, next_check_at=10300))
        self.tick()
        self.assertEqual(self.probe.call_count, 1)
        content = self.path.read_text()
        self.assertNotIn("secret", content)
        self.assertNotIn("example.com", content)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_401_recovers_only_after_new_token_and_success(self):
        self.probe.side_effect = [(401, {}, None), (200, {}, {"account": {"uuid": "uuid-one"}})]
        self.gateway.run_credential_checks_now.side_effect = lambda pool: self.tokens.update(one="secret-new")
        self.assertEqual(self.tick()["status"], "valid")
        self.assertEqual(self.probe.call_args_list[1].args, ("secret-new",))
        self.gateway._observe.assert_called_once()
        self.gateway._persist.assert_called_once()
        self.assertEqual(self.gateway.run_credential_checks_now.call_args.args[0].profiles, [self.profile])

    def test_401_without_rotation_remains_expired(self):
        self.probe.return_value = (401, {}, None)
        self.assertEqual(self.tick()["status"], "expired")
        self.assertEqual(self.probe.call_count, 1)
        self.gateway.run_credential_checks_now.assert_called_once()

    def test_401_replacement_rejected_no_recursive_refresh(self):
        self.probe.return_value = (401, {}, None)
        self.gateway.run_credential_checks_now.side_effect = lambda pool: self.tokens.update(one="secret-new")
        self.assertEqual(self.tick()["status"], "expired")
        self.assertEqual(self.probe.call_count, 2)
        self.assertEqual(self.gateway._observe.call_count, 2)
        self.gateway.run_credential_checks_now.assert_called_once()

    def test_concurrent_rotation_does_not_reject_new_credential(self):
        def response(token):
            if token == "secret-old":
                self.tokens["one"] = "secret-new"
                return 401, {}, None
            return 200, {}, {"account": {"uuid": "uuid-one"}}
        self.probe.side_effect = response
        self.assertEqual(self.tick()["status"], "valid")
        self.gateway._observe.assert_not_called()
        self.gateway.run_credential_checks_now.assert_not_called()

    def test_success_for_outdated_credential_is_pending(self):
        def response(token):
            self.tokens["one"] = "secret-new"
            return 200, {}, {"account": {"uuid": "uuid-one"}}
        self.probe.side_effect = response
        self.assertEqual(self.tick()["status"], "pending")
        self.assertIsNone(self.m.profiles["one"]["last_success_at"])

    def test_403_not_expired_and_no_refresh(self):
        self.probe.return_value = (403, {}, None)
        self.assertEqual(self.tick()["status"], "forbidden")
        self.gateway._observe.assert_not_called()
        self.gateway.run_credential_checks_now.assert_not_called()

    def test_uuid_mismatch_and_malformed_success_not_valid(self):
        for data in ({"account": {"uuid": "other"}}, {}, [], {"account": None}):
            self.probe.return_value = (200, {}, data)
            self.m.profiles.clear()
            self.assertEqual(self.tick()["status"], "error")

    def test_429_provider_backoff_survives_restart_and_new_accounts(self):
        self.probe.return_value = (429, {"Retry-After": "900"}, None)
        self.assertEqual(self.tick()["next_check_at"], 10900)
        self.pool.profiles.append(monitor.config.Profile("two", "new", account_uuid="uuid-two"))
        self.tokens["two"] = "secret-two"
        restarted = monitor.Monitor(self.path)
        restarted._request = self.probe
        self.now = 10300
        restarted.tick(self.gateway)
        self.assertEqual(self.probe.call_count, 1)
        self.assertEqual(restarted.profiles["two"]["status"], "pending")
        self.assertEqual(json.loads(self.path.read_text())["provider_next_check_at"], 10900)

    def test_retry_after_http_date(self):
        self.probe.return_value = (429, {"Retry-After": "Thu, 01 Jan 1970 03:03:20 GMT"}, None)
        self.assertEqual(self.tick()["next_check_at"], 11000)

    def test_network_backoff_survives_restart_and_preserves_success_time(self):
        self.tick()
        self.now = 10300
        self.probe.side_effect = OSError("secret must never be printed")
        self.assertEqual(self.tick()["next_check_at"], 10600)
        self.m = monitor.Monitor(self.path)
        self.m._request = self.probe
        self.now = 10600
        state = self.tick()
        self.assertEqual(state["next_check_at"], 11200)
        self.assertEqual(state["last_success_at"], 10000)

    def test_500_error_and_secret_exception_never_logged(self):
        self.probe.side_effect = RuntimeError("secret-old")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(self.tick()["status"], "error")
        self.assertEqual(output.getvalue(), "")
        self.now += 300
        self.probe.side_effect = None
        self.probe.return_value = (503, {}, None)
        self.assertEqual(self.tick()["status"], "error")

    def test_one_account_each_tick_disabled_and_non_oauth_skipped(self):
        self.pool.profiles.extend([
            monitor.config.Profile("two", "two", account_uuid="uuid-two"),
            monitor.config.Profile("disabled", "disabled", enabled=False),
            monitor.config.Profile("api", "api", kind="api"),
        ])
        self.tokens["two"] = "secret-two"
        self.tick()
        self.assertEqual(self.probe.call_count, 1)
        self.assertEqual(set(self.m.profiles), {"one", "two"})
        self.now += 30
        self.tick()
        self.assertEqual(self.probe.call_count, 2)
        self.profile.enabled = False
        self.now += 300
        self.tick()
        self.assertEqual(self.probe.call_args.args, ("secret-two",))

    def test_transport_readonly_get_bounded_response_and_http_errors(self):
        response = Mock(status=200, headers={})
        response.read.return_value = b'{"account":{"uuid":"uuid-one"}}'
        self.request.side_effect = None
        self.request.return_value.__enter__ = Mock(return_value=response)
        self.request.return_value.__exit__ = Mock(return_value=False)
        self.assertEqual(monitor.Monitor._request("secret-old")[0], 200)
        req = self.request.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.full_url, monitor.PROFILE_URL)
        self.assertIsNone(req.data)
        response.read.assert_called_once_with(65536)
        body = io.BytesIO(b"secret error")
        self.request.side_effect = urllib.error.HTTPError(monitor.PROFILE_URL, 401, "no", {}, body)
        self.assertEqual(monitor.Monitor._request("secret-old"), (401, {}, None))
        self.assertTrue(body.closed)

    def test_tick_survives_pool_error(self):
        with patch.object(monitor.config, "load_pool", side_effect=ValueError("secret")):
            self.m.tick(self.gateway)
        self.probe.assert_not_called()

    def test_redirect_handler_refuses_forwarding_credentials(self):
        self.assertIsNone(monitor._NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://unrelated.invalid"))

    def test_recovery_success_does_not_claim_valid_when_recheck_forbidden(self):
        self.probe.side_effect = [(401, {}, None), (403, {}, None)]
        self.gateway.run_credential_checks_now.side_effect = lambda pool: self.tokens.update(one="secret-new")
        state = self.tick()
        self.assertEqual(state["status"], "forbidden")
        self.assertIsNone(state["last_success_at"])

    def test_first_observation_initializes_from_full_pool(self):
        self.pool.profiles.append(monitor.config.Profile("two", "two"))
        self.gateway._runtime.clear()
        self.gateway._sync_snapshot = Mock(return_value=SimpleNamespace(profiles=[
            SimpleNamespace(profile_id="one"), SimpleNamespace(profile_id="two")]))
        self.probe.return_value = (401, {}, None)
        self.tick()
        self.assertEqual(set(self.gateway._runtime), {"one", "two"})
        self.gateway._sync_snapshot.assert_called_once_with(self.pool)

    def test_rotation_during_rejection_clears_real_gateway_auth_invalid(self):
        from claude_unlimited.gateway import Gateway

        class RotateOnFirstLock:
            def __init__(inner):
                inner.first = True

            def __enter__(inner):
                if inner.first:
                    inner.first = False
                    self.tokens["one"] = "secret-new"

            def __exit__(inner, *args):
                return False

        self.gateway._lock = RotateOnFirstLock()
        self.gateway._current_profile_id = "one"
        self.gateway._observe = lambda *args: Gateway._observe(self.gateway, *args)
        self.probe.side_effect = [(401, {}, None), (200, {}, {"account": {"uuid": "uuid-one"}})]
        # Shared refresh owner is a no-op (another caller rotated the token).
        self.assertEqual(self.tick()["status"], "valid")
        self.assertEqual(self.gateway._runtime["one"].state, ProfileState.ELIGIBLE)
        self.gateway._queue_recovered_usage_read.assert_called_once_with("one")
        self.assertEqual(self.gateway._persist.call_count, 2)
        self.assertEqual(self.probe.call_count, 2)

    def test_valid_identity_preserves_non_auth_runtime_states(self):
        for state in (ProfileState.DISABLED, ProfileState.EXHAUSTED, ProfileState.COOLDOWN,
                      ProfileState.DRAINING, ProfileState.ELIGIBLE):
            with self.subTest(state=state):
                self.gateway._runtime["one"] = ProfileRuntime("one", 1, 98, True, state=state)
                self.m.profiles.clear()
                self.assertEqual(self.tick()["status"], "valid")
                self.assertEqual(self.gateway._runtime["one"].state, state)
        self.gateway._queue_recovered_usage_read.assert_not_called()
        self.gateway._persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
