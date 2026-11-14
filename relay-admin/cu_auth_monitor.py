"""Read-only OAuth identity checks, driven by the daemon's existing timer.

Install as claude_unlimited.auth_monitor. Gateway remains the sole owner of
credential renewal; this module never exchanges refresh tokens or sends messages.
All persisted timestamps are UTC Unix seconds, independent of process uptime.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request

from . import config, oauth_credential, secret_store
from .anthropic_oauth import PROFILE_URL
from .observation import AuthInvalid
from .router import ProfileState

INTERVAL = 300
MAX_BACKOFF = 3600
STATUSES = {"valid", "expired", "forbidden", "rate_limited", "error", "pending"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect())


def _timestamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


class Monitor:
    def __init__(self, path: Path | None = None):
        self.path = path or config.APP_DIR / "auth_monitor_state.json"
        self.profiles = {}
        self.provider_next_check_at = 0
        try:
            raw = json.loads(self.path.read_text())
            self.provider_next_check_at = _timestamp(raw.get("provider_next_check_at")) or 0
            for pid, item in raw.get("profiles", {}).items():
                if isinstance(item, dict) and item.get("status") in STATUSES:
                    self.profiles[pid] = {
                        "status": item["status"],
                        **{key: _timestamp(item.get(key)) for key in
                           ("checked_at", "last_success_at", "next_check_at")},
                    }
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".auth-monitor-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"profiles": self.profiles,
                           "provider_next_check_at": self.provider_next_check_at}, stream,
                          allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _token(pid):
        stored = secret_store.get_token(pid)
        return oauth_credential.decode(stored).access_token if stored else None

    @staticmethod
    def _request(token):
        request = urllib.request.Request(PROFILE_URL, headers={"Authorization": f"Bearer {token}"})
        try:
            with _opener.open(request, timeout=15) as response:
                return response.status, response.headers, json.loads(response.read(65536))
        except urllib.error.HTTPError as exc:
            # Never read, persist or log provider error bodies (may contain PII).
            code, headers = exc.code, exc.headers
            exc.close()
            return code, headers, None

    @staticmethod
    def _reject(gateway, profile, pool, token):
        # A concurrent refresh must not invalidate the replacement credential.
        # Secret backends can block; do not hold the gateway's routing lock.
        # Repository writes have no common credential lock, so compare as close
        # to observation as possible and re-read after recovery below.
        if Monitor._token(profile.id) != token:
            return False
        with gateway._lock:
            if profile.id not in gateway._runtime:
                gateway._runtime = {rt.profile_id: rt for rt in gateway._sync_snapshot(pool).profiles}
            gateway._observe(profile.id, AuthInvalid(), datetime.now(timezone.utc))
        gateway._persist()
        return True

    def _check(self, gateway, profile, pool):
        stored = secret_store.get_token(profile.id)
        if not stored:
            return "pending", None
        token = gateway._maybe_refresh_credential(profile, stored)
        code, headers, data = self._request(token)
        if code == 401:
            if self._reject(gateway, profile, pool, token):
                gateway.run_credential_checks_now(replace(pool, profiles=[profile]))
            replacement = self._token(profile.id)
            if not replacement:
                return "pending", None
            if replacement == token:
                return "expired", None
            # One recheck only, and only when the shared owner actually rotated.
            token = replacement
            code, headers, data = self._request(token)
            if code == 401:
                return ("expired" if self._reject(gateway, profile, pool, token)
                        else "pending"), None
        if self._token(profile.id) != token:
            return "pending", None
        if code == 200:
            account = data.get("account") if isinstance(data, dict) else None
            if isinstance(account, dict) and profile.account_uuid and account.get("uuid") == profile.account_uuid:
                # A concurrent renewal can complete after our rejection but
                # before the shared recovery check. Its verified replacement
                # must also clear the rejection we installed in the router.
                recovered = False
                with gateway._lock:
                    current = gateway._runtime.get(profile.id)
                    if current is not None and current.state == ProfileState.AUTH_INVALID:
                        gateway._runtime[profile.id] = replace(current, state=ProfileState.ELIGIBLE)
                        gateway._queue_recovered_usage_read(profile.id)
                        recovered = True
                if recovered:
                    gateway._persist()
                return "valid", None
            return "error", None
        if code == 403:
            return "forbidden", None
        if code == 429:
            retry = headers.get("Retry-After") if headers else None
            deadline = time.time() + INTERVAL
            if retry:
                try:
                    seconds = float(retry)
                    if math.isfinite(seconds):
                        deadline = max(deadline, time.time() + seconds)
                except ValueError:
                    try:
                        deadline = max(deadline, parsedate_to_datetime(retry).timestamp())
                    except (ValueError, TypeError, OverflowError):
                        pass
            return "rate_limited", deadline
        return "error", None

    def tick(self, gateway):
        """Check at most one enabled OAuth profile. Never escape into the timer."""
        try:
            self._tick(gateway)
        except Exception:
            # Deliberately no exception text: secret stores and HTTP errors may
            # embed credentials. An existing observation expires by its schedule.
            return

    def _tick(self, gateway):
        now = time.time()
        pool = config.load_pool()
        candidates = [p for p in pool.profiles if p.enabled and p.kind == "oauth"]
        changed = False
        for profile in candidates:
            if profile.id not in self.profiles:
                self.profiles[profile.id] = dict(status="pending", checked_at=None,
                                                 last_success_at=None, next_check_at=now)
                changed = True
        if now < self.provider_next_check_at:
            if changed:
                self._save()
            return
        due = [p for p in candidates if (self.profiles[p.id]["next_check_at"] or 0) <= now]
        if not due:
            if changed:
                self._save()
            return
        profile = min(due, key=lambda p: self.profiles[p.id]["next_check_at"] or 0)
        old = self.profiles[profile.id]
        try:
            status, deadline = self._check(gateway, profile, pool)
        except Exception:
            status, deadline = "error", None
        checked_at = time.time()
        delay = INTERVAL
        if status == "error" and old["status"] == "error" and old["checked_at"] is not None:
            delay = min(MAX_BACKOFF, max(INTERVAL, (old["next_check_at"] or 0) - old["checked_at"]) * 2)
        next_check_at = max(checked_at + delay, deadline or 0)
        if status == "rate_limited":
            self.provider_next_check_at = next_check_at
        self.profiles[profile.id] = dict(status=status, checked_at=checked_at,
                                         last_success_at=checked_at if status == "valid" else old["last_success_at"],
                                         next_check_at=next_check_at)
        self._save()


_monitor = None


def tick(gateway):
    """Daemon integration: call once after runtime_snapshot(), every 30 seconds."""
    global _monitor
    try:
        if _monitor is None:
            _monitor = Monitor()
        _monitor.tick(gateway)
    except Exception:
        return
