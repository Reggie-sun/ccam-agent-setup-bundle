#!/usr/bin/env python3
"""Authenticated, narrowly scoped UI for the local CU account pool."""
import base64
import hashlib
import hmac
import http.client
from http.cookies import SimpleCookie, CookieError
import json
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
# Optional email -> local ccam alias (work, work2, ...) for display only; generated
# on the admin's machine by make-aliases.py. Re-read per request so updates need no restart.
ALIAS_FILE = Path(os.environ.get('ALIAS_FILE', str(ROOT / 'aliases.json')))
FONT_FILES = { '/fonts/' + name: ROOT / 'fonts' / name for name in
               ('Barlow-Regular.ttf', 'BarlowCondensed-SemiBold.ttf') }
ORIGIN = os.environ.get('ADMIN_ORIGIN', 'https://relay-admin.example.com')
PUBLIC_HOST = urlsplit(ORIGIN).hostname
LOGIN_SOCKET = os.environ.get('LOGIN_SOCKET', '/run/relay-login/login.sock')
AUTH = json.loads(Path(os.environ.get('ADMIN_AUTH_FILE', str(Path(os.environ.get('CREDENTIALS_DIRECTORY', '/etc/relay-admin')) / 'admin-auth'))).read_text())
CSRF = secrets.token_urlsafe(32)
AUTH_SLOTS = threading.BoundedSemaphore(2)
# Browsers resend the same Basic header on every request. Remember recently verified headers
# (as per-process HMAC digests, in memory only) so page loads do not compete for the scrypt slots.
# Unverified headers still go through the bounded scrypt path unchanged.
AUTH_CACHE = {}
AUTH_CACHE_LOCK = threading.Lock()
AUTH_CACHE_TTL = 600
AUTH_CACHE_MAX = 4
AUTH_CACHE_KEY = secrets.token_bytes(32)  # per-process; AUTH is only loaded at start, so a password change (restart) clears the cache
PROFILE_WRITE_LOCK = threading.Lock()
INTERNAL_OWNER = secrets.token_hex(32)
def load_aliases():
    try:
        data = json.loads(ALIAS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {email.lower(): alias for email, alias in data.items()
            if isinstance(email, str) and isinstance(alias, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', alias)}


FIELDS = ('id', 'name', 'enabled', 'priority', 'state', 'usage_5h_percent',
          'usage_7d_percent', 'usage_5h_resets_at', 'usage_7d_resets_at', 'last_used_at',
          'usage_observed_at')


def upstream(path, method='GET', data=None, csrf=None):
    headers = {'Content-Type': 'application/json'}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    request = Request('http://127.0.0.1:4317' + path, method=method,
                      headers=headers, data=json.dumps(data).encode() if data is not None else None)
    with urlopen(request, timeout=15) as response:
        return json.load(response)


class LoginConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(LOGIN_SOCKET)


def login_request(path, method, owner, data=None):
    connection = LoginConnection('localhost', timeout=20)
    try:
        body = json.dumps(data).encode() if data is not None else None
        connection.request(method, path, body=body, headers={
            'Content-Type': 'application/json', 'X-Login-Owner': owner})
        response = connection.getresponse()
        payload = response.read(32769)
        if len(payload) > 32768:
            raise ValueError('broker_response_too_large')
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError('invalid_broker_response')
        # Never pass arbitrary broker/CLI output or credentials to the browser.
        safe = {key: result[key] for key in (
            'login_id', 'status', 'authorization_url', 'expires_at', 'profile_id',
            'error_code', 'error') if key in result}
        if path == '/internal/status':
            safe = {'active': result.get('active')}
        if path in ('/internal/probe-health', '/internal/auth-health'):
            safe = {'profiles': result.get('profiles', {})}
        return response.status, safe
    finally:
        connection.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log auth headers, account identifiers or request bodies.

    def send(self, status, payload, html=False, content_type=None):
        body = payload if html or content_type else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type or ('text/html; charset=utf-8' if html else 'application/json; charset=utf-8'))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, 'new_login_owner', None):
            self.send_header('Set-Cookie', '__Host-relay_login=' + self.new_login_owner + '; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=1800')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if status == 401:
            self.send_header('WWW-Authenticate', 'Basic realm="VPS Account Admin", charset="UTF-8"')
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self):
        host = self.headers.get('Host', '').split(':')[0]
        if host not in (PUBLIC_HOST, '127.0.0.1', 'localhost'):
            self.send(400, {'error': 'invalid_host'})
            return False
        if host == PUBLIC_HOST and self.headers.get('X-Forwarded-Proto') == 'http':
            self.send_response(308)
            self.send_header('Location', ORIGIN + '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return False
        raw = self.headers.get('Authorization', '')
        valid = False
        key = hmac.new(AUTH_CACHE_KEY, raw.encode(), hashlib.sha256).digest()
        with AUTH_CACHE_LOCK:
            expiry = AUTH_CACHE.get(key)
            valid = expiry is not None and expiry > time.monotonic()
            if expiry is not None and not valid:
                del AUTH_CACHE[key]
        if not valid and raw.startswith('Basic ') and len(raw) < 2048:
            try:
                user, password = base64.b64decode(raw[6:], validate=True).decode().split(':', 1)
                if not AUTH_SLOTS.acquire(blocking=False):
                    self.send(503, {'error': 'authentication_busy'})
                    return False
                try:
                    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(AUTH['salt']), n=16384, r=8, p=1).hex()
                    valid = hmac.compare_digest(user.encode(), AUTH['username'].encode()) and hmac.compare_digest(digest, AUTH['hash'])
                finally:
                    AUTH_SLOTS.release()
            except (ValueError, UnicodeError):
                pass
            if valid:
                now = time.monotonic()
                with AUTH_CACHE_LOCK:
                    for stale in [k for k, expiry in AUTH_CACHE.items() if expiry <= now]:
                        del AUTH_CACHE[stale]
                    if len(AUTH_CACHE) < AUTH_CACHE_MAX:
                        AUTH_CACHE[key] = now + AUTH_CACHE_TTL
        if not valid:
            self.send(401, {'error': 'authentication_required'})
        return valid

    def login_owner(self, create=False):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            value = cookie['__Host-relay_login'].value if '__Host-relay_login' in cookie else ''
        except CookieError:
            value = ''
        if re.fullmatch(r'[a-f0-9]{64}', value):
            return value
        if create:
            self.new_login_owner = secrets.token_hex(32)
            return self.new_login_owner
        return None

    def forward_login(self, method, data=None):
        owner = self.login_owner()
        if owner is None:
            self.send(409, {'error': 'reload_required'})
            return
        try:
            status, result = login_request(self.path, method, owner, data)
            self.send(status, result)
        except (OSError, http.client.HTTPException, ValueError):
            self.send(502, {'error': 'login_operation_unconfirmed' if method == 'POST' else 'login_status_unavailable'})

    def do_POST(self):
        if not self.authenticated():
            return
        if self.headers.get('Origin') != ORIGIN or not hmac.compare_digest(self.headers.get('X-CSRF-Token', '').encode(), CSRF.encode()):
            self.send(403, {'error': 'invalid_origin_or_csrf'})
            return
        action = re.fullmatch(r'/api/logins/([a-f0-9]{64})/(code|cancel)', self.path)
        presence = self.path == '/api/presence'
        if not presence and self.path != '/api/logins' and not action:
            self.send(404, {'error': 'not_found'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            if presence:
                if data:
                    raise ValueError()
            elif self.path == '/api/logins':
                if data == {'mode': 'add'}:
                    pass
                elif set(data) == {'mode', 'profile_id'} and data['mode'] == 'reauth' and isinstance(data['profile_id'], str) and re.fullmatch(r'[a-f0-9]{16,64}', data['profile_id']):
                    pass
                else:
                    raise ValueError()
            elif action[2] == 'cancel':
                if data:
                    raise ValueError()
            elif set(data) != {'code'} or not isinstance(data['code'], str) or not 8 <= len(data['code']) <= 2048 or any(ord(c) < 32 or ord(c) == 127 for c in data['code']):
                raise ValueError()
        except (ValueError, UnicodeError):
            self.send(400, {'error': 'invalid_presence_request' if presence else 'invalid_login_request'})
            return
        if presence:
            try:
                token = upstream('/api/status')['csrf_token']
                result = upstream('/api/presence', 'POST', {}, token)
                if not isinstance(result, dict) or result.get('active') is not True:
                    raise ValueError('presence_unconfirmed')
            except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
                self.send(502, {'error': 'presence_unconfirmed'})
                return
            self.send(200, {'active': True})
            return
        with PROFILE_WRITE_LOCK:
            self.forward_login('POST', data)

    def do_GET(self):
        if not self.authenticated():
            return
        if self.path == '/':
            self.send(200, (ROOT / 'index.html').read_bytes(), html=True)
        elif self.path in FONT_FILES:
            try:
                self.send(200, FONT_FILES[self.path].read_bytes(), content_type='font/ttf')
            except OSError:
                self.send(404, {'error': 'font_not_found'})
        elif self.path == '/api/profiles':
            self.login_owner(create=True)
            try:
                profiles = upstream('/api/profiles')['profiles']
                warnings = {}
                try:
                    status, health = login_request('/internal/probe-health', 'GET', INTERNAL_OWNER)
                    if status == 200 and isinstance(health.get('profiles'), dict):
                        warnings = health['profiles']
                except (OSError, http.client.HTTPException, ValueError):
                    pass  # A missing observation must remain unknown, not healthy.
                auth = {}
                try:
                    status, health = login_request('/internal/auth-health', 'GET', INTERNAL_OWNER)
                    if status == 200 and isinstance(health.get('profiles'), dict):
                        auth = health['profiles']
                except (OSError, http.client.HTTPException, ValueError):
                    pass
                distribute = None
                try:
                    value = upstream('/api/settings').get('settings', {}).get('distribute_sessions_default')
                    distribute = value if type(value) is bool else None
                except (OSError, http.client.HTTPException, ValueError, AttributeError):
                    pass
                current = None
                try:
                    # cu's rotation pointer: where new requests go next. Missing stays unknown.
                    pointer = upstream('/api/status').get('current_profile_id')
                    if isinstance(pointer, str) and re.fullmatch(r'[a-f0-9]{16,64}', pointer):
                        current = pointer
                except (OSError, http.client.HTTPException, ValueError, AttributeError):
                    pass
                items = []
                aliases = load_aliases()
                for item in profiles:
                    public = {key: item.get(key) for key in FIELDS}
                    if item.get('usage_window_label') == 'weekly':
                        # cu stores a weekly-only allowance in its primary slot.
                        public['usage_7d_percent'] = public['usage_5h_percent']
                        public['usage_7d_resets_at'] = public['usage_5h_resets_at']
                        public['usage_5h_percent'] = None
                        public['usage_5h_resets_at'] = None
                    if 'usage_observed_at' not in item:
                        # cu without the quota-age patch: omit it so the page hides the age instead of showing "unknown".
                        del public['usage_observed_at']
                    warning = warnings.get(item.get('id'), {})
                    public['probe_status'] = warning.get('probe_status', 'unknown')
                    public['next_probe_at'] = warning.get('next_probe_at')
                    observation = auth.get(item.get('id'), {})
                    public['auth_status'] = observation.get('auth_status', 'pending')
                    public['alias'] = aliases.get(str(item.get('name', '')).lower())
                    for key in ('auth_checked_at', 'auth_last_success_at', 'auth_next_check_at'):
                        public[key] = observation.get(key)
                    items.append(public)
                self.send(200, {'profiles': items, 'current_profile_id': current, 'distribute': distribute, 'csrf_token': CSRF})
            except (OSError, http.client.HTTPException, ValueError, KeyError):
                self.send(502, {'error': 'account_service_unavailable'})
        elif self.path == '/api/logins/current' or re.fullmatch(r'/api/logins/[a-f0-9]{64}', self.path):
            self.forward_login('GET')
        else:
            self.send(404, {'error': 'not_found'})

    def do_PATCH(self):
        with PROFILE_WRITE_LOCK:
            self.patch_profile()

    def patch_profile(self):
        if not self.authenticated():
            return
        if self.headers.get('Origin') != ORIGIN or not hmac.compare_digest(self.headers.get('X-CSRF-Token', '').encode(), CSRF.encode()):
            self.send(403, {'error': 'invalid_origin_or_csrf'})
            return
        mode_change = self.path == '/api/settings/distribute'
        if not mode_change and not re.fullmatch(r'/api/profiles/[a-f0-9]{16,64}', self.path):
            self.send(404, {'error': 'not_found'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1024 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or len(data) != 1:
                raise ValueError()
            if 'enabled' in data:
                if type(data['enabled']) is not bool:
                    raise ValueError()
            elif mode_change:
                raise ValueError()
            elif 'priority' in data:
                if type(data['priority']) is not int or not 1 <= data['priority'] <= 999:
                    raise ValueError()
            else:
                raise ValueError()
        except (ValueError, UnicodeError):
            self.send(400, {'error': 'invalid_change'})
            return
        try:
            status, login = login_request('/internal/status', 'GET', INTERNAL_OWNER)
            if status != 200 or type(login.get('active')) is not bool:
                raise ValueError('invalid_login_status')
            if login['active']:
                self.send(409, {'error': 'login_in_progress'})
                return
        except (OSError, http.client.HTTPException, ValueError):
            self.send(503, {'error': 'login_status_unavailable'})
            return
        try:
            token = upstream('/api/status')['csrf_token']
            if mode_change:
                # The only cu setting this page may change: per-session spreading.
                upstream('/api/settings', 'PATCH', {'distribute_sessions_default': data['enabled']}, token)
            else:
                upstream(self.path, 'PATCH', data, token)
            self.send(200, {'ok': True})
        except HTTPError as error:
            status = error.code if error.code in (400, 404, 429) else 502
            self.send(status, {'error': 'account_update_failed'})
        except (OSError, http.client.HTTPException, ValueError, KeyError):
            self.send(502, {'error': 'account_service_unavailable'})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '4318'))), Handler).serve_forever()
