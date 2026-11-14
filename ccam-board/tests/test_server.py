import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class BoardTests(unittest.TestCase):
    def test_port_cannot_be_shared_by_two_servers(self):
        with server.BoardServer((server.HOST, 0), server.Handler) as first:
            with self.assertRaises(OSError):
                server.BoardServer(first.server_address, server.Handler)

    def test_browser_disconnect_is_quiet_but_other_errors_propagate(self):
        handler = object.__new__(server.Handler)
        for error in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            with self.subTest(error=error), patch.object(server.BaseHTTPRequestHandler, "handle", side_effect=error):
                handler.close_connection = False
                handler.handle()
                self.assertTrue(handler.close_connection)
        with patch.object(server.BaseHTTPRequestHandler, "handle", side_effect=RuntimeError("unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                handler.handle()

    def test_home_override(self):
        with patch.dict(os.environ, {"CCAM_HOME": "sandbox"}):
            self.assertEqual(server.state_paths(), (Path("sandbox/state/usage"), Path("sandbox/run")))

    def test_xdg_absolute(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"CCAM_HOME": "", "XDG_STATE_HOME": root}):
                self.assertEqual(server.state_paths()[0], Path(root) / "ccam/usage")

    def test_relative_xdg_ignored(self):
        with patch.dict(os.environ, {"CCAM_HOME": "", "XDG_STATE_HOME": "relative"}):
            self.assertEqual(server.state_paths()[0], Path.home() / ".local/state/ccam/usage")

    def test_time_unknown_and_valid(self):
        for value in (None, "invalid", "0001-01-01T00:00:00Z"):
            self.assertIsNone(server.parse_time(value))
        self.assertIsNotNone(server.parse_time("2026-01-01T00:00:00Z"))

    def test_run_json_utf8_and_errors(self):
        with patch.object(server.subprocess, "run", return_value=Mock(returncode=0, stdout='[{"name":"测试"}]')) as run:
            self.assertEqual(server.run_json(["ccam"])[0]["name"], "测试")
            self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        with patch.object(server.subprocess, "run", return_value=Mock(returncode=1, stderr="failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                server.run_json(["ccam"])

    def test_registered_sessions_and_stale_markers(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "profile-test"
            folder.mkdir()
            (folder / "123").write_text("100")
            (folder / "456").write_text("old")
            with patch.object(server, "LOCKS", Path(root)), patch.object(server.sys, "platform", "win32"), patch.object(server, "process_start", return_value="100"):
                self.assertEqual(server.running("test"), 1)
            self.assertTrue((folder / "456").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows API")
    def test_real_windows_process(self):
        self.assertTrue(server.process_start(os.getpid()).isdigit())
        self.assertIsNone(server.process_start(0))

    def test_unsupported_tracking_is_unknown(self):
        with patch.object(server.sys, "platform", "darwin"):
            self.assertIsNone(server.running("test"))

    def test_windows_launch_has_no_shell(self):
        name = 'test & echo unwanted'
        with patch.object(server.sys, "platform", "win32"), patch.object(server.shutil, "which", return_value="C:/space path/ccam.exe"), patch.object(server.subprocess, "CREATE_NEW_CONSOLE", 16, create=True), patch.object(server.subprocess, "Popen") as launch:
            server.open_account(name)
            launch.assert_called_once_with(["C:/space path/ccam.exe", "run", name], creationflags=16)

    def test_missing_binary(self):
        with patch.object(server.shutil, "which", return_value=None):
            with self.assertRaisesRegex(OSError, "PATH"):
                server.open_account("test")

    def test_snapshot_and_utf8_statusline(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            (state / "测试.statusline.json").write_text(json.dumps({"at": "2026-02-01T00:00:00Z", "limits": {"weekly": {"known": True, "percent": 30}}}), encoding="utf-8")
            usage = [{"name": "测试", "at": "2026-01-01T00:00:00Z", "status": "ok", "limits": {}}]
            with patch.object(server, "STATE", state), patch.object(server, "run_json", side_effect=[usage, [{"name": "测试", "slot": 1}]]), patch.object(server, "running", return_value=2), patch.object(server, "next_account", return_value=("测试", "reason")):
                snapshot = server.take_snapshot()
            self.assertFalse(snapshot["loading"])
            self.assertEqual(snapshot["headroom"], "测试")
            self.assertTrue(snapshot["accounts"][0]["live"])
            self.assertEqual(snapshot["accounts"][0]["weekly"]["percent"], 30)
            self.assertEqual(snapshot["accounts"][0]["running"], 2)
            (state / "broken.statusline.json").write_text("bad")
            with patch.object(server, "STATE", state):
                self.assertIsNone(server.read_statusline("broken"))

    def test_linux_launch(self):
        with patch.object(server.sys, "platform", "linux"), patch.object(server.shutil, "which", return_value="/bin/ccam"), patch.object(server.subprocess, "Popen") as launch:
            server.open_account("test")
            launch.assert_called_once_with(["gnome-terminal", "--", "/bin/ccam", "run", "test"], start_new_session=True)

    def test_post_invalid_and_launch_failure(self):
        handler = object.__new__(server.Handler)
        handler.path = "/api/open"
        handler.send_json = Mock()
        for payload in (b"[]", b"invalid"):
            handler.headers = {"Content-Length": str(len(payload))}
            handler.rfile = io.BytesIO(payload)
            handler.do_POST()
            self.assertEqual(handler.send_json.call_args.args[0], 400)
        payload = json.dumps({"name": "test"}).encode()
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        with patch.object(server, "_snapshot", {"accounts": [{"name": "test"}]}), patch.object(server, "open_account", side_effect=OSError("missing terminal")):
            handler.do_POST()
            self.assertEqual(handler.send_json.call_args.args, (500, {"error": "missing terminal"}))


if __name__ == "__main__":
    unittest.main()
