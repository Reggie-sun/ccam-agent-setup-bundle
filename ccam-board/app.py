#!/usr/bin/python3
"""ccam desktop app: a native GTK4 window around a web page (static/board.html).

One row per account: whether it can be used now, what is left of its five-hour
and weekly quota, when a full one comes back, and the tokens per second it is
burning (burn.py). Python owns the data and the actions; the page only draws
and posts back clicks.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import gi

# Ubuntu 24.04 限制非特权用户命名空间，WebKit 的 bwrap 沙盒起不来；页面只加载本地文件

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, GLib, WebKit  # noqa: E402

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import burn  # noqa: E402
import server  # noqa: E402

REFRESH_SECONDS = 20
SAMPLE_SECONDS = 2  # token 速率采样间隔
CONFIG = Path.home() / ".config/ccam/config.toml"
# ccam 的 auto_strategy：值 → (按钮文字, 说明)
MODES = {
    "weekly-reset": ("分散使用", "5 小时额度还剩 60% 以上的账号里，每周最先重置的优先；会话多又已用过半的账号会避开"),
    "drain": ("用完再切", "只用每周最先重置的账号，它满了才换下一个"),
}
STRATEGY_LINE = re.compile(r"^auto_strategy\s*=.*$", re.M)


def read_mode():
    match = STRATEGY_LINE.search(CONFIG.read_text())
    value = match.group(0).split("=", 1)[1].strip().strip("'\"") if match else ""
    return value if value in MODES else "weekly-reset"


def write_mode(mode):
    text = CONFIG.read_text()
    line = f"auto_strategy = '{mode}'"
    text = STRATEGY_LINE.sub(line, text) if STRATEGY_LINE.search(text) else f"{line}\n{text}"
    CONFIG.write_text(text)


def seconds_left(iso):
    moment = server.parse_time(iso)
    if moment is None:
        return None
    return max(int((moment - datetime.now(timezone.utc)).total_seconds()), 0)


def remaining(iso):
    seconds = seconds_left(iso)
    if seconds is None:
        return "未开始计时"
    if seconds == 0:
        return "即将重置"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}天{hours}小时后"
    if hours:
        return f"{hours}小时{minutes}分后"
    return f"{minutes}分后"


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Claude 账号额度", default_width=1260, default_height=840)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.busy = False
        self.snapshot = None
        self.ready = False
        self.tracker = burn.Tracker()

        self.web = WebKit.WebView()
        background = Gdk.RGBA()
        background.parse("#0b0d10")
        self.web.set_background_color(background)
        settings = self.web.get_settings()
        settings.set_allow_file_access_from_file_urls(True)
        manager = self.web.get_user_content_manager()
        manager.register_script_message_handler("ccam", None)
        manager.connect("script-message-received::ccam", self.on_message)
        self.web.connect("load-changed", self.on_load)
        self.web.load_uri((ROOT / "static/board.html").as_uri())

        self.toast = Adw.ToastOverlay(child=self.web)
        header = Adw.HeaderBar(css_classes=["flat"])
        view = Adw.ToolbarView(content=self.toast)
        view.add_top_bar(header)
        self.set_content(view)
        self.refresh()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self.tick)
        GLib.timeout_add_seconds(SAMPLE_SECONDS, self.sample)

    def on_load(self, _web, event):
        if event == WebKit.LoadEvent.FINISHED:
            self.ready = True
            self.push()

    def on_message(self, _manager, value):
        message = json.loads(value.to_string())
        if message["action"] == "mode" and message["mode"] in MODES and message["mode"] != read_mode():
            write_mode(message["mode"])
            self.toast.add_toast(Adw.Toast(title=f"已切换为「{MODES[message['mode']][0]}」模式"))
            self.refresh(no_cache=True)
        elif message["action"] == "refresh":
            self.refresh(no_cache=True)
        elif message["action"] == "open":
            account = next((a for a in self.snapshot["accounts"] if a["name"] == message["name"]), None)
            if account:
                self.open_account(account)

    def tick(self):
        self.refresh()
        return True

    def sample(self):
        if self.snapshot:
            self.tracker.sample([a["name"] for a in self.snapshot["accounts"]])
            self.push()
        return True

    def refresh(self, no_cache=False):
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                snapshot, error = server.take_snapshot(no_cache=no_cache), None
            except Exception as exc:
                snapshot, error = None, str(exc)
            GLib.idle_add(self.done, snapshot, error)

        threading.Thread(target=work, daemon=True).start()

    def done(self, snapshot, error):
        self.busy = False
        if error:
            self.toast.add_toast(Adw.Toast(title=f"刷新失败：{error}"))
            return False
        self.snapshot = snapshot
        for account in snapshot["accounts"]:
            if account.get("status") != "expired":
                self.tracker.note_quota(account["name"], account["five_hour"]["percent"] or 0)
        self.push()
        return False

    def state(self):
        mode = read_mode()
        chosen = self.snapshot["headroom"]
        accounts = []
        for a in self.snapshot["accounts"]:
            five = round(a["five_hour"]["percent"] or 0)
            # 只在此刻真的在消耗时估算用满时间；停下来的账号额度不会再涨
            rate = self.tracker.rate(a["name"])
            eta = None if five >= 100 or rate < 1 else self.tracker.minutes_to_full(a["name"])
            accounts.append({
                "name": a["name"],
                "best": a["name"] == chosen,
                "stale": a.get("status") == "expired",
                "running": a.get("running", 0),
                "rate": round(rate, 1),
                "history": [round(v) for v in self.tracker.history(a["name"])],
                "etaMin": None if eta is None else round(eta, 1),
                "eta": "" if eta is None or eta > 600 else "按此速度马上用满" if eta < 1 else
                       f"按此速度约 {int(eta)} 分钟用满" if eta < 90 else f"按此速度约 {eta / 60:.1f} 小时用满",
                "five": {"percent": five, "reset": remaining(a["five_hour"]["resets_at"]),
                         "secs": seconds_left(a["five_hour"]["resets_at"])},
                "week": {"percent": round(a["weekly"]["percent"] or 0), "reset": remaining(a["weekly"]["resets_at"]),
                         "secs": seconds_left(a["weekly"]["resets_at"])},
            })
        return {"mode": mode, "modeHint": MODES[mode][1] + "。",
                "modes": [[k, v[0], v[1]] for k, v in MODES.items()],
                "stamp": "更新于 " + datetime.now().strftime("%H:%M:%S"), "accounts": accounts}

    def push(self):
        if not (self.ready and self.snapshot):
            return
        script = f"window.update({json.dumps(self.state(), ensure_ascii=False)})"
        self.web.evaluate_javascript(script, -1, None, None, None, None, None)

    def open_account(self, account):
        name = account["name"]
        result = subprocess.run(["ccam", "use", name], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.toast.add_toast(Adw.Toast(title=(result.stderr or result.stdout or "切换失败").strip()))
            return
        shortcut = account.get("shortcut")
        program = shutil.which(shortcut) if shortcut else None
        subprocess.Popen(["gnome-terminal", "--", *([program] if program else ["ccam", "run", name])],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.toast.add_toast(Adw.Toast(title=f"已切换到 {name}"))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="local.ccam.Board")

    def do_activate(self):
        if not self.props.active_window:
            Window(self)
        self.props.active_window.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
