import ast
from datetime import datetime, timezone
import hashlib
import io
import re
import os
from types import SimpleNamespace
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import install_cu_patch as installer

DIFF = installer.PATCH_FILE.read_text()
STREAM_DIFF = installer.STREAM_PATCH_FILE.read_text()


def sides(prefix):
    """A synthetic daemon built from the real patch's hunks: (original, patched)."""
    old, new = [], []
    for hunk in DIFF.split('\n@@ ')[1:] + installer.RESET_PATCH_FILE.read_text().split('\n@@ ')[1:]:
        lines = hunk.splitlines(keepends=True)[1:]
        old.append(''.join(line[1:] for line in lines if line[0] in ' -'))
        new.append(''.join(line[1:] for line in lines if line[0] in ' +'))
    # Kept inside a string literal so the installer's compile() check passes on the synthetic file.
    wrap = lambda parts: (prefix + "TEXT = r'''\n" + '\n# filler\n'.join(parts) + "'''\n").encode()
    baseline = wrap(old)
    v6 = installer.apply_hunks(baseline.decode(), DIFF).encode()
    return baseline, v6, wrap(new)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class CuPatchTests(unittest.TestCase):
    def setUp(self):
        # Two accepted baselines, like cu 1.4.0 and cu 1.4.0 + auth monitor.
        self.plain, self.plain_v6, self.plain_after = sides('# plain\n')
        self.monitor, self.monitor_v6, self.monitor_after = sides('# with auth monitor\n')
        sources = {sha(self.plain): sha(self.plain_v6), sha(self.monitor): sha(self.monitor_v6)}
        reset = patch.object(installer, 'RESET_SOURCES', {sha(self.plain_v6): sha(self.plain_after), sha(self.monitor_v6): sha(self.monitor_after)})
        reset.start()
        self.addCleanup(reset.stop)
        # The synthetic v6 and reset fixtures are literal patch context, not a real
        # module. Keep this third-stage fixture independent of their overlapping hunks.
        with tempfile.NamedTemporaryFile('w', delete=False) as stream:
            stream.write("--- a/daemon.py\n+++ b/daemon.py\n@@ -1 +1 @@\n-TEXT = r'''\n+STREAM = r'''\n")
        self.addCleanup(os.unlink, stream.name)
        self.plain_reset, self.monitor_reset = self.plain_after, self.monitor_after
        self.plain_after, self.monitor_after = [data.replace(b"TEXT = r", b"STREAM = r", 1)
                                               for data in (self.plain_reset, self.monitor_reset)]
        patches = [patch.object(installer, 'STREAM_PATCH_FILE', Path(stream.name)),
                   patch.object(installer, 'STREAM_SOURCES', {sha(self.plain_reset): sha(self.plain_after),
                                                               sha(self.monitor_reset): sha(self.monitor_after)})]
        for replacement in patches:
            replacement.start()
            self.addCleanup(replacement.stop)
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

    @unittest.skipUnless(os.environ.get('CU_140_SOURCE'), 'set CU_140_SOURCE to an upstream cu 1.4.0 daemon.py')
    def test_installed_quota_candidates(self):
        original = Path(os.environ['CU_140_SOURCE']).read_text()
        installed = installer.apply_hunks(installer.apply_hunks(original, DIFF), installer.RESET_PATCH_FILE.read_text())
        fn = next(x for x in ast.parse(installed).body if getattr(x, 'name', None) == '_usage_probe_candidates')
        now = 1000
        # priority, state, observed_at, 5h reset, weekly reset
        rows = [(1, 'ready', 990, now, None), (2, 'ready', 995, None, now),
                (3, 'ready', now, now, None), (4, 'ready', 999, now + 1, None),
                (5, 'exhausted', 940, None, None), (6, 'draining', 941, None, None),
                (7, 'auth_invalid', 900, now, now), (8, 'disabled', 900, now, now)]
        runtime, observed = {}, {}
        for ident, state, read_at, hour, week in rows:
            runtime[ident] = SimpleNamespace(state=SimpleNamespace(value=state),
                resets_at=datetime.fromtimestamp(hour, timezone.utc) if hour else None,
                resets_at_7d=datetime.fromtimestamp(week, timezone.utc) if week else None)
            observed[ident] = read_at
        gw = SimpleNamespace(runtime_snapshot=lambda: runtime, usage_observed_at=lambda: observed,
                             take_usage_recheck_requests=lambda: set())
        probe = SimpleNamespace(provider_for=lambda p: 'anthropic', Candidate=lambda i, provider, stamp: (i, stamp))
        env = dict(_gateway=gw, usage_probe=probe, time=SimpleNamespace(time=lambda: now))
        exec(compile(ast.Module([fn], []), 'daemon.py', 'exec'), env)
        pool = SimpleNamespace(profiles=[SimpleNamespace(id=row[0], enabled=True) for row in rows])
        self.assertEqual(env['_usage_probe_candidates'](pool),
                         [(5, None), (6, 941), (1, None), (2, None), (4, 999), (3, now)])

    @unittest.skipUnless(os.environ.get('CU_140_SOURCE'), 'requires cu 1.4.0 daemon.py')
    def test_stream_end_diagnostics(self):
        source = Path(os.environ['CU_140_SOURCE']).read_text()
        for diff in (DIFF, installer.RESET_PATCH_FILE.read_text(), STREAM_DIFF):
            source = installer.apply_hunks(source, diff)
        handler = next(n for n in ast.parse(source).body if getattr(n, 'name', None) == '_DashboardHandler')
        method = next(n for n in handler.body if getattr(n, 'name', None) == '_write_proxy_body')
        records = []
        env = dict(re=re, _append_proxy_log=lambda name, kind, fields: records.append(fields),
                   _discard_result=lambda result: result.body_chunks.close())
        exec(compile(ast.Module([method], []), 'daemon.py', 'exec'), env)
        begin = b'event: message_start\ndata: {}\n\n'
        stop = b'event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n'
        for mode, expected, received, written in [
                ('split', 'complete', True, True), ('truncated', 'upstream_eof_without_stop', False, False),
                ('read_fail', 'upstream_error', False, False), ('write_fail', 'downstream_write_error', True, False)]:
            with self.subTest(mode=mode):
                def body():
                    yield begin
                    if mode == 'read_fail':
                        raise ConnectionResetError()
                    if mode == 'split':
                        yield from (bytes([n]) for n in stop)
                    else:
                        yield stop[:-2] if mode == 'truncated' else stop

                class Sink(io.BytesIO):
                    def write(self, data):
                        if mode == 'write_fail' and data == stop:
                            raise BrokenPipeError()
                        return super().write(data)

                output = Sink()
                result = SimpleNamespace(body_chunks=body(), status=200, headers={
                    'Request-ID': 'req_test', 'content-type': 'text/event-stream'})
                call = lambda: env['_write_proxy_body'](SimpleNamespace(path='/v1/messages', wfile=output), result)
                if mode in ('read_fail', 'write_fail'):
                    with self.assertRaises(OSError):
                        call()
                else:
                    call()
                self.assertEqual(records[-1], dict(request_id='req_test', outcome=expected,
                                                    message_stop_received=received, message_stop_written=written))
                if mode == 'split':
                    self.assertEqual(output.getvalue(), begin + stop)

    def test_wrong_result_hash_is_refused(self):
        with patch.object(installer, 'SOURCES', {sha(self.plain): '0' * 64}):
            with self.assertRaises(ValueError):
                installer.patch_daemon(self.plain, DIFF, installer.SOURCES)

    def test_both_baselines_patch_backup_idempotence_and_rollback(self):
        for original, expected in ((self.plain, self.plain_after), (self.monitor, self.monitor_after),
                                   (self.plain_v6, self.plain_after), (self.monitor_v6, self.monitor_after),
                                   (self.plain_reset, self.plain_after), (self.monitor_reset, self.monitor_after)):
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
