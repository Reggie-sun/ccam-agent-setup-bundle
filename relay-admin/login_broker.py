#!/usr/bin/env python3
"""Private Unix-socket bridge to the official Claude CLI's interactive login."""
import hmac
import grp
import json
import math
import os
from pathlib import Path
import pty
import re
import secrets
import select
import signal
import socketserver
import stat
import subprocess
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

ACTIVE = {'starting', 'waiting', 'submitting'}
ID = re.compile(r'[a-f0-9]{64}')
PROFILE_ID = re.compile(r'[a-f0-9]{16,64}')
CODE = re.compile(r'[A-Za-z0-9._~+/=#%\-]{8,2048}')
URL = re.compile(r'https://[^\x00-\x20\x7f-\x9f<>"\']+')
TTL = 600


def probe_health(path):
    """Read scheduler warnings, never infer success from an absent warning."""
    try:
        profiles = json.loads(path.read_text()).get('profiles', {})
        if not isinstance(profiles, dict):
            return {}
    except (OSError, ValueError, AttributeError):
        return {}
    result = {}
    for profile_id, entry in profiles.items():
        if not PROFILE_ID.fullmatch(profile_id) or not isinstance(entry, dict):
            continue
        kind = entry.get('kind')
        if kind not in ('auth', 'rate_limited', 'failure'):
            continue
        scheduled = entry.get('not_before')
        result[profile_id] = {
            'probe_status': kind,
            'next_probe_at': scheduled if type(scheduled) in (int, float) and math.isfinite(scheduled) and scheduled > 0 else None,
        }
    return result


def auth_health(path):
    """Expose only persisted automatic-check observations, never credentials."""
    try:
        profiles = json.loads(path.read_text()).get('profiles', {})
        if not isinstance(profiles, dict):
            return {}
    except (OSError, ValueError, AttributeError):
        return {}
    result = {}
    for profile_id, entry in profiles.items():
        if not PROFILE_ID.fullmatch(profile_id) or not isinstance(entry, dict):
            continue
        if entry.get('status') not in ('valid', 'expired', 'forbidden', 'rate_limited', 'error', 'pending'):
            continue
        public = {'auth_status': entry['status']}
        for key in ('checked_at', 'last_success_at', 'next_check_at'):
            value = entry.get(key)
            public['auth_' + key] = value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None
        result[profile_id] = public
    return result


class LoginError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def authorization_url(output):
    """Only the official CLI URL shape observed on version 2.1.322 is exposed."""
    for candidate in URL.findall(output):
        parsed = urlsplit(candidate)
        if parsed.netloc != 'claude.com' or parsed.path != '/cai/oauth/authorize' or parsed.fragment:
            continue
        query = parse_qs(parsed.query)
        allowed = {'client_id', 'response_type', 'redirect_uri', 'scope', 'code_challenge', 'code_challenge_method', 'state', 'code'}
        if (not set(query) <= allowed or any(len(values) != 1 for values in query.values())):
            continue
        if (query.get('response_type') == ['code'] and query.get('code_challenge_method') == ['S256']
                and query.get('redirect_uri') == ['https://platform.claude.com/oauth/code/callback']
                and all(len(query.get(key, [])) == 1 and query[key][0] for key in ('state', 'code_challenge', 'client_id'))):
            return candidate
    return None


def daemon_environment():
    """Reuse the same unlocked Secret Service as the running cu service."""
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            if b'claude_unlimited' not in (process / 'cmdline').read_bytes().split(b'\0'):
                continue
            env = dict(part.decode().split('=', 1) for part in (process / 'environ').read_bytes().split(b'\0') if b'=' in part)
            bus = env.get('DBUS_SESSION_BUS_ADDRESS', '')
            if bus.startswith('unix:'):
                return {'DBUS_SESSION_BUS_ADDRESS': bus, 'XDG_RUNTIME_DIR': env.get('XDG_RUNTIME_DIR', '/run/claude-relay')}
        except (OSError, UnicodeError):
            continue
    raise LoginError(503, 'account_service_unavailable')


class CuAccounts:
    def __init__(self):
        from claude_unlimited import anthropic_oauth, profiles, config, cli
        self.oauth, self.profiles, self.config, self.cli = anthropic_oauth, profiles, config, cli
        self.uncertain_path = config.APP_DIR / 'web-login-uncertain.json'

    def target(self, profile_id):
        target = self.config.load_pool().get(profile_id)
        if target is None or target.kind != 'oauth' or not target.account_uuid:
            raise LoginError(400, 'invalid_reauth_target')
        return target.account_uuid

    def prepare(self):
        os.environ.update(daemon_environment())
        root = self.config.CLAUDE_ACCOUNTS_DIR
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='web-login-', dir=root))
        return directory

    def spawn(self, directory):
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        env = {**os.environ, 'CLAUDE_CONFIG_DIR': str(directory), 'TERM': 'dumb', 'NO_COLOR': '1',
               'BROWSER': '/bin/true', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        # Credentials and relay routing must not influence this isolated official login.
        for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_OAUTH_TOKEN'):
            env.pop(key, None)
        try:
            process = subprocess.Popen(['/opt/claude-relay/claude', 'auth', 'login'], stdin=slave,
                                       stdout=slave, stderr=slave, env=env, start_new_session=True)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        return process, master

    def resolve(self, directory):
        imported = self.oauth.read_claude_code_credentials(config_dir=directory)
        account = self.oauth.fetch_account_profile(imported.access_token)
        return imported, account

    def commit(self, record, resolved):
        imported, account = resolved
        if record['mode'] == 'reauth':
            target = self.config.load_pool().get(record['target_id'])
            if (target is None or target.kind != 'oauth' or target.account_uuid != record['target_uuid']
                    or account.account_uuid != record['target_uuid']):
                raise LoginError(409, 'account_mismatch')
            existing = self.profiles.find_by_account_uuid(account.account_uuid)
            if existing is None or existing.id != target.id:
                raise LoginError(409, 'account_mismatch')
            name = target.name
        else:
            name = account.email or 'Imported Claude account'
        # This call may partially persist before raising; callers must not retry it.
        record['commit_started'] = True
        self.uncertain(record)
        profile, _ = self.profiles.upsert_oauth_profile(
            name=name, account_uuid=account.account_uuid, credential=imported.access_token,
            plan=self.oauth.plan_from_account(account), refresh_token=imported.refresh_token,
            expires_at=imported.expires_at, claude_config_dir=str(record['directory']))
        return profile.id

    def finish(self, directory):
        self.cli._drop_plaintext_login(directory, '.credentials.json')
        if (directory / '.credentials.json').exists():
            raise OSError('Login credential cleanup incomplete')

    def discard(self, directory):
        self.cli._discard_login_dir(directory, claude=True)
        if directory.exists():
            raise OSError('Login directory cleanup incomplete')

    def uncertain(self, record):
        # No credential data: prevents an automatic restart from silently retrying an uncertain commit.
        payload = {key: record[key] for key in ('login_id', 'owner', 'expires_at')}
        payload['directory'] = str(record['directory'])
        fd, temporary = tempfile.mkstemp(prefix='.web-login-marker-', dir=self.uncertain_path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.uncertain_path)
            self.sync_directory()
        finally:
            Path(temporary).unlink(missing_ok=True)

    def sync_directory(self):
        fd = os.open(self.uncertain_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def clear_uncertain(self):
        self.uncertain_path.unlink(missing_ok=True)
        self.sync_directory()


class Logins:
    def __init__(self, accounts, clock=time.time):
        self.accounts, self.clock = accounts, clock
        self.lock = threading.RLock()
        self.records = {}
        self.blocked = False
        marker = getattr(accounts, 'uncertain_path', None)
        if marker and marker.exists():
            self.blocked = True
            try:
                previous = json.loads(marker.read_text())
                if ID.fullmatch(previous['login_id']) and ID.fullmatch(previous['owner']):
                    previous.update(status='unknown', error_code='profile_save_unknown')
                    self.records[previous['login_id']] = previous
            except (ValueError, KeyError, TypeError, OSError):
                pass  # A malformed recovery marker still blocks writes.

    def public(self, record):
        result = {key: record[key] for key in ('login_id', 'status', 'expires_at')}
        for key in ('authorization_url', 'profile_id', 'error_code'):
            if record.get(key):
                result[key] = record[key]
        return result

    def terminal(self, record, status, error=None):
        record['status'] = status
        record.pop('authorization_url', None)
        if error:
            record['error_code'] = error

    def unknown(self, record, error):
        self.blocked = True
        self.terminal(record, 'unknown', error)
        try:
            self.accounts.uncertain(record)
        except Exception:
            pass  # Remain blocked in memory even if recovery-marker storage fails.

    def lookup(self, owner, login_id):
        record = self.records.get(login_id)
        if not record or not hmac.compare_digest(record['owner'], owner):
            raise LoginError(404, 'login_not_found')
        return record

    def active(self):
        with self.lock:
            return self.blocked or any(r['status'] in ACTIVE or not r.get('worker_done', True) for r in self.records.values())

    def current(self, owner):
        with self.lock:
            records = [r for r in self.records.values() if hmac.compare_digest(r['owner'], owner)]
            return self.public(records[-1]) if records else {'status': 'idle'}

    def start(self, owner, data):
        if not isinstance(data, dict) or data.get('mode') not in ('add', 'reauth'):
            raise LoginError(400, 'invalid_login_request')
        mode = data['mode']
        if set(data) != ({'mode'} if mode == 'add' else {'mode', 'profile_id'}):
            raise LoginError(400, 'invalid_login_request')
        if mode == 'reauth' and (not isinstance(data['profile_id'], str) or not PROFILE_ID.fullmatch(data['profile_id'])):
            raise LoginError(400, 'invalid_reauth_target')
        with self.lock:
            if self.active():
                raise LoginError(409, 'login_in_progress' if not self.blocked else 'login_result_unknown')
            # Retain a small, bounded recovery window, including terminal results.
            self.records = {k: r for k, r in self.records.items() if r['expires_at'] + TTL > self.clock()}
            while len(self.records) >= 32:
                self.records.pop(next(iter(self.records)))
            record = {'login_id': secrets.token_hex(32), 'owner': owner, 'mode': mode,
                      'status': 'starting', 'expires_at': self.clock() + TTL, 'commit_started': False, 'worker_done': False}
            if mode == 'reauth':
                record.update(target_id=data['profile_id'], target_uuid=self.accounts.target(data['profile_id']))
            self.records[record['login_id']] = record
            threading.Thread(target=self.run, args=(record,), daemon=True).start()
            return self.public(record)

    def get(self, owner, login_id):
        with self.lock:
            return self.public(self.lookup(owner, login_id))

    def submit(self, owner, login_id, data):
        if not isinstance(data, dict) or set(data) != {'code'} or not isinstance(data['code'], str) or not CODE.fullmatch(data['code']):
            raise LoginError(400, 'invalid_authorization_code')
        with self.lock:
            record = self.lookup(owner, login_id)
            if record['status'] != 'waiting' or record['expires_at'] <= self.clock():
                raise LoginError(409, 'login_not_waiting')
            record['status'] = 'submitting'
            record.pop('authorization_url', None)
            try:
                attrs = termios.tcgetattr(record['fd'])
                attrs[3] &= ~termios.ECHO
                termios.tcsetattr(record['fd'], termios.TCSANOW, attrs)
                raw = (data['code'] + '\n').encode()
                if os.write(record['fd'], raw) != len(raw):
                    raise OSError('incomplete PTY write')
            except (OSError, termios.error):
                self.blocked = True
                self.terminal(record, 'unknown', 'code_submission_unknown')
            return self.public(record)

    def cancel(self, owner, login_id, data):
        if data != {}:
            raise LoginError(400, 'invalid_login_request')
        with self.lock:
            record = self.lookup(owner, login_id)
            if record['status'] in ACTIVE:
                self.terminal(record, 'cancelled')
            return self.public(record)

    @staticmethod
    def stop(process):
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        except ProcessLookupError:
            pass

    def run(self, record):
        process = None
        fd = None
        try:
            directory = self.accounts.prepare()
            record['directory'] = directory
            with self.lock:
                if record['status'] not in ACTIVE:
                    return
                # A killed CLI can leave plaintext login credentials. Journal its owned
                # directory before launch so restart requires explicit recovery.
                self.accounts.uncertain(record)
                process, fd = self.accounts.spawn(directory)
                record['fd'] = fd
            output = ''
            while True:
                with self.lock:
                    if record['status'] not in ACTIVE:
                        return
                    if self.clock() >= record['expires_at']:
                        self.terminal(record, 'expired', 'login_expired')
                        return
                if select.select([fd], [], [], .2)[0]:
                    try:
                        chunk = os.read(fd, 8192)
                    except OSError:
                        chunk = b''
                    output = (output + chunk.decode(errors='replace'))[-32768:]
                    url = authorization_url(output)
                    with self.lock:
                        if record['status'] == 'starting' and url and 'Paste code here' in output:
                            record['authorization_url'] = url
                            record['status'] = 'waiting'
                            output = ''
                result = process.poll()
                if result is not None:
                    break
            if result != 0:
                with self.lock:
                    if record['status'] in ACTIVE:
                        self.terminal(record, 'failed', 'official_login_failed')
                return
            resolved = self.accounts.resolve(directory)
            with self.lock:
                if record['status'] not in ACTIVE:
                    return
                if self.clock() >= record['expires_at']:
                    self.terminal(record, 'expired', 'login_expired')
                    return
                profile_id = self.accounts.commit(record, resolved)
                record['profile_id'] = profile_id
                self.accounts.finish(directory)
                self.accounts.clear_uncertain()
                self.terminal(record, 'succeeded')
        except Exception as error:
            with self.lock:
                if record.get('commit_started'):
                    self.unknown(record, 'profile_save_unknown')
                elif record['status'] in ACTIVE:
                    self.terminal(record, 'failed', error.code if isinstance(error, LoginError) else 'official_login_failed')
        finally:
            stopped = True
            try:
                self.stop(process)
            except Exception:
                stopped = False
                with self.lock:
                    self.unknown(record, 'login_process_stop_unknown')
            if fd is not None:
                os.close(fd)
            with self.lock:
                record.pop('fd', None)
                directory = record.get('directory')
                if directory is not None and stopped:
                    try:
                        if record.get('commit_started'):
                            self.accounts.finish(directory)
                        else:
                            self.accounts.discard(directory)
                            self.accounts.clear_uncertain()
                    except Exception:
                        self.unknown(record, 'login_cleanup_failed')
                    else:
                        if record.get('error_code') == 'code_submission_unknown' and not record.get('commit_started'):
                            # The owned CLI is stopped and no account import was attempted.
                            self.blocked = False
                            self.terminal(record, 'failed', 'code_submission_failed')
                record['worker_done'] = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        owner = self.headers.get('X-Login-Owner', '')
        if not ID.fullmatch(owner):
            raise LoginError(403, 'invalid_login_owner')
        manager = self.server.logins
        if self.command == 'GET':
            if self.path == '/internal/auth-health':
                return {'profiles': auth_health(manager.accounts.config.APP_DIR / 'auth_monitor_state.json')}
            if self.path == '/internal/probe-health':
                return {'profiles': probe_health(manager.accounts.config.APP_DIR / 'usage_probe_state.json')}
            if self.path == '/internal/status':
                return {'active': manager.active()}
            if self.path == '/api/logins/current':
                return manager.current(owner)
            match = re.fullmatch(r'/api/logins/([a-f0-9]{64})', self.path)
            if match:
                return manager.get(owner, match[1])
        if self.command == 'POST':
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                raise LoginError(400, 'invalid_login_request') from None
            if self.path == '/api/logins':
                return manager.start(owner, data)
            match = re.fullmatch(r'/api/logins/([a-f0-9]{64})/(code|cancel)', self.path)
            if match:
                action = manager.submit if match[2] == 'code' else manager.cancel
                return action(owner, match[1], data)
        raise LoginError(404, 'not_found')

    def handle_request(self):
        try:
            self.send(200, self.dispatch())
        except LoginError as error:
            self.send(error.status, {'error': error.code})
        except Exception:
            self.send(503, {'error': 'login_service_unavailable'})

    do_GET = do_POST = handle_request


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def handle_error(self, *_):
        pass


if __name__ == '__main__':
    os.umask(0o077)
    path = Path('/run/relay-login/login.sock')
    admin_gid = grp.getgrnam('relay-admin').gr_gid
    # Keep the cu user's primary GID for /proc ptrace checks; share only this
    # broker's IPC directory/socket with the web service's supplementary group.
    os.chown(path.parent, -1, admin_gid)
    os.chmod(path.parent, 0o750)
    if path.exists():
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise SystemExit('Login socket path is not a socket')
        path.unlink()
    with Server(str(path), Handler) as server:
        os.chown(path, -1, admin_gid)
        os.chmod(path, 0o660)
        server.logins = Logins(CuAccounts())
        server.serve_forever()
