#!/usr/bin/env python3
"""Local board for ccam. Binds to 127.0.0.1 only."""

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 47321
ROOT = Path(__file__).resolve().parent


def state_paths():
    """Match ccam/internal/paths, including isolated CCAM_HOME installs."""
    root = os.environ.get("CCAM_HOME")
    if root:
        return Path(root) / "state/usage", Path(root) / "run"
    xdg = Path(os.environ.get("XDG_STATE_HOME", ""))
    base = xdg if xdg.is_absolute() else Path.home() / ".local/state"
    return base / "ccam/usage", base / "ccam/locks"


STATE, LOCKS = state_paths()
ENDPOINT_EVERY = 20
RENEW_EVERY = 300
FILE_EVERY = 2

_last_renew = 0.0
_lock = threading.Lock()
_snapshot = {"accounts": [], "headroom": None, "error": None, "loading": True}
_listeners = []
_listeners_lock = threading.Lock()


def parse_time(value):
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if moment.year < 2020:
        return None
    return moment


def run_json(args):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ccam failed").strip())
    return json.loads(result.stdout or "[]")


def read_statusline(name):
    path = STATE / f"{name}.statusline.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    measured = parse_time(data.get("at"))
    limits = data.get("limits") or {}
    if measured is None or not limits:
        return None
    return {"at": measured, "limits": limits}


def window(raw):
    raw = raw or {}
    resets = raw.get("resets_at")
    return {
        "percent": raw.get("percent"),
        "known": bool(raw.get("known")),
        "resets_at": resets if parse_time(resets) else None,
    }


def overlay(account, line):
    measured = parse_time(account.get("at"))
    if line is None or measured is None or line["at"] <= measured:
        account["measured_at"] = account.get("at")
        account["live"] = False
        return account
    limits = account.setdefault("limits", {})
    for key in ("five_hour", "weekly"):
        incoming = line["limits"].get(key) or {}
        if incoming.get("known"):
            limits[key] = incoming
    account["at"] = line["at"].isoformat()
    account["measured_at"] = account["at"]
    account["source"] = "statusline"
    account["live"] = True
    return account


def process_start(pid):
    """Use ccam's platform-specific marker format; never substitute PID existence."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return None
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return str((ticks - 116444736000000000) * 100)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    return None


def running(name):
    """Count verified registered sessions; unsupported platforms report unknown."""
    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        return None
    count = 0
    for marker in (LOCKS / f"profile-{name}").glob("[0-9]*"):
        try:
            started = process_start(int(marker.name))
            expected = marker.read_text(encoding="utf-8").strip()
        except (OSError, IndexError, ValueError):
            continue
        count += started is not None and started == expected
    return count


def next_account():
    """Ask ccam's account selection policy without recording a selection."""
    result = subprocess.run(["ccam", "pick", "--dry-run", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=25, check=False)
    if result.returncode != 0:  # 75：没有账号有余量
        return None, None
    picked = json.loads(result.stdout)
    return picked.get("name"), picked.get("reason")


def take_snapshot(no_cache=False):
    global _last_renew
    args = ["ccam", "usage", "--json"]
    if no_cache:
        args.append("--no-cache")
    usage = run_json(args)
    if any(item.get("status") == "expired" for item in usage) and time.monotonic() - _last_renew >= RENEW_EVERY:
        _last_renew = time.monotonic()
        usage = run_json([*args, "--no-cache", "--refresh"] if "--no-cache" not in args else [*args, "--refresh"])
    listed = {item["name"]: item for item in run_json(["ccam", "list", "--json"])}
    accounts = []
    for item in usage:
        name = item["name"]
        meta = listed.get(name, {})
        account = overlay(item, read_statusline(name))
        account["email"] = meta.get("email")
        account["shortcut"] = meta.get("shortcut")
        account["default"] = bool(meta.get("default"))
        account["org"] = meta.get("org")
        account["slot"] = meta.get("slot", item.get("slot"))
        account["running"] = running(name)
        limits = account.get("limits") or {}
        account["five_hour"] = window(limits.get("five_hour"))
        account["weekly"] = window(limits.get("weekly"))
        account["scoped"] = [
            {"label": scope.get("label") or scope.get("model") or "模型", **window(scope)}
            for scope in limits.get("scoped") or []
        ]
        accounts.append(account)
    accounts.sort(key=lambda item: item.get("slot") or 0)
    chosen, reason = next_account()
    return {
        "accounts": accounts,
        "headroom": chosen,
        "headroom_reason": reason,
        "error": None,
        "loading": False,
        "polled_at": datetime.now().astimezone().isoformat(),
    }


def publish(snapshot):
    global _snapshot
    payload = json.dumps(snapshot, ensure_ascii=False).encode()
    with _lock:
        _snapshot = snapshot
    with _listeners_lock:
        dead = []
        for listener in _listeners:
            try:
                listener.put_nowait(payload)
            except queue.Full:
                dead.append(listener)
        for listener in dead:
            _listeners.remove(listener)


def refresh_loop():
    next_endpoint = 0
    last_files = None
    while True:
        now = time.monotonic()
        files = tuple(sorted((path.name, path.stat().st_mtime_ns) for path in STATE.glob("*.statusline.json"))) if STATE.exists() else ()
        due = now >= next_endpoint or files != last_files
        if due:
            try:
                publish(take_snapshot())
                if now >= next_endpoint:
                    next_endpoint = now + ENDPOINT_EVERY
                last_files = files
            except Exception as error:
                with _lock:
                    current = dict(_snapshot)
                current["loading"] = False
                current["error"] = str(error)
                publish(current)
                next_endpoint = now + ENDPOINT_EVERY
        time.sleep(FILE_EVERY)


def open_account(name):
    # Always invoke the native binary, never a shortcut script or shell command.
    executable = shutil.which("ccam.exe" if sys.platform == "win32" else "ccam")
    if not executable:
        raise OSError("ccam not found in PATH")
    args = [executable, "run", name]
    if sys.platform == "win32":
        return subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
    if sys.platform.startswith("linux"):
        return subprocess.Popen(["gnome-terminal", "--", *args], start_new_session=True)
    raise OSError("Open a terminal and run ccam run <name> on this platform")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Windows browsers may abort a persistent socket between requests.
            self.close_connection = True

    def log_message(self, fmt, *args):
        return

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = (ROOT / "static/index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/snapshot":
            with _lock:
                current = _snapshot
            self.send_json(200, current)
            return
        if path == "/api/events":
            self.close_connection = False
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            box = queue.Queue(maxsize=8)
            with _listeners_lock:
                _listeners.append(box)
            with _lock:
                current = json.dumps(_snapshot, ensure_ascii=False).encode()

            def chunk(payload):
                self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()

            try:
                chunk(b"data: " + current + b"\n\n")
                while True:
                    try:
                        payload = box.get(timeout=15)
                    except queue.Empty:
                        chunk(b": ping\n\n")
                        continue
                    chunk(b"data: " + payload + b"\n\n")
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                pass
            finally:
                with _listeners_lock:
                    if box in _listeners:
                        _listeners.remove(box)
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/refresh":
            try:
                publish(take_snapshot(no_cache=True))
            except Exception as error:
                self.send_json(500, {"error": str(error)})
                return
            with _lock:
                current = _snapshot
            self.send_json(200, current)
            return
        if path != "/api/open":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= 4096:
                raise ValueError("invalid length")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected object")
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "bad json"})
            return
        name = body.get("name")
        with _lock:
            accounts = _snapshot.get("accounts") or []
        match = next((item for item in accounts if item.get("name") == name), None)
        if match is None:
            self.send_json(404, {"error": "unknown account"})
            return
        try:
            open_account(name)
        except OSError as error:
            self.send_json(500, {"error": str(error)})
            return
        self.send_json(200, {"ok": True})


class BoardServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR permits two servers to listen on the same port.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    server = BoardServer((HOST, PORT), Handler)
    threading.Thread(target=refresh_loop, daemon=True).start()
    print(f"http://{HOST}:{PORT}", flush=True)
    if "--open-browser" in sys.argv:
        webbrowser.open(f"http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
