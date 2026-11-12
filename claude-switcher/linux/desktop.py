#!/usr/bin/python3
"""Native GTK 3 desktop for Claude Switcher. No web server or embedded browser."""
from pathlib import Path
import shutil
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gio, GLib, Gtk, Gdk, GdkX11

from desktop_support import Backend, CHECK_NAMES, parse_checks


CSS = b"""
window { background: #f4f5f7; color: #25313c; }
.title { font-size: 25px; font-weight: bold; }
.muted { color: #667582; }
.card { background: white; border-radius: 12px; border: 1px solid #dfe4e8; padding: 18px; }
.account { font-size: 18px; font-weight: bold; }
.status { font-size: 14px; font-weight: bold; }
button { padding: 9px 16px; border-radius: 7px; }
.primary { background: #235f56; color: white; border: none; }
.primary:hover { background: #174b43; }
.ok { color: #237755; }
.warn { color: #92610a; }
.fail { color: #b63e3e; }
treeview { background: white; }
treeview.view:selected { background: #dcece7; color: #25313c; }
"""


def label(text, style=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    if style:
        widget.get_style_context().add_class(style)
    return widget


class Window(Gtk.ApplicationWindow):
    def __init__(self, app, backend=None):
        super().__init__(application=app, title="claude检测器")
        self.add_events(Gdk.EventMask.PROPERTY_CHANGE_MASK)
        self.backend = backend or Backend()
        self.busy = False
        self.set_default_size(860, 760)
        self.set_size_request(660, 600)
        icon = Path(__file__).resolve().parents[1] / "assets/icon-2048.png"
        if icon.is_file():
            self.set_icon_from_file(str(icon))
        self.connect("delete-event", self.on_close)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=24)
        self.add(outer)
        header = Gtk.Box(spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        titles.pack_start(label("claude检测器", "title"), False, False, 0)
        titles.pack_start(label("Linux 桌面版 · 浏览器账号与环境检查", "muted"), False, False, 0)
        header.pack_start(titles, True, True, 0)
        self.refresh_button = Gtk.Button(label="刷新状态")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_button, False, False, 0)
        outer.pack_start(header, False, False, 0)

        self.notice = label("")
        outer.pack_start(self.notice, False, False, 0)
        accounts = Gtk.Box(spacing=14, homogeneous=True)
        self.account_labels = {}
        self.switch_buttons = []
        for slot in ("A", "B"):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            card.get_style_context().add_class("card")
            card.pack_start(label("账号 " + slot, "account"), False, False, 0)
            self.account_labels[slot] = label("", "muted")
            card.pack_start(self.account_labels[slot], False, False, 0)
            button = Gtk.Button(label="切换到账号 " + slot)
            button.connect("clicked", lambda _, target=slot: self.switch(target))
            self.switch_buttons.append(button)
            card.pack_end(button, False, False, 0)
            accounts.pack_start(card, True, True, 0)
        outer.pack_start(accounts, False, False, 0)
        outer.pack_start(label("切换会更换整个 Chrome/Chromium 配置目录，不切换 Claude Code 登录。\n请先完全退出浏览器；首次切换会将现有浏览器资料归入账号 A。", "muted"), False, False, 0)

        toolbar = Gtk.Box(spacing=10)
        self.check_button = Gtk.Button(label="开始安全检查")
        self.check_button.get_style_context().add_class("primary")
        self.check_button.connect("clicked", lambda *_: self.start("checkdetail"))
        toolbar.pack_start(self.check_button, False, False, 0)
        self.fix_button = Gtk.Button(label="在终端中修复…")
        self.fix_button.connect("clicked", self.fix)
        toolbar.pack_start(self.fix_button, False, False, 0)
        self.spinner = Gtk.Spinner()
        toolbar.pack_end(self.spinner, False, False, 0)
        outer.pack_start(toolbar, False, False, 0)

        self.summary = label("尚未检查", "status")
        outer.pack_start(self.summary, False, False, 0)
        self.model = Gtk.ListStore(str, str, str, str)
        for name in CHECK_NAMES:
            self.model.append([name, "未检查", "—", "#667582"])
        self.tree = Gtk.TreeView(model=self.model)
        self.tree.set_headers_visible(True)
        for index, title in enumerate(("检查项", "结果", "详情")):
            cell = Gtk.CellRendererText()
            if index == 2:
                cell.props.wrap_width = 420
                cell.props.wrap_mode = 2
            col = Gtk.TreeViewColumn(title, cell, text=index)
            if index == 1:
                col.add_attribute(cell, "foreground", 3)
            col.set_expand(index == 2)
            col.set_min_width((110, 80, 260)[index])
            self.tree.append_column(col)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(self.tree)
        outer.pack_start(scroller, True, True, 0)
        self.footer = label("点击“开始安全检查”获取当前结果；检查通过不代表账号或登录可用。", "muted")
        outer.pack_end(self.footer, False, False, 0)
        self.refresh()

    def message(self, title, detail, confirm=False):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING if confirm else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK_CANCEL if confirm else Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(detail)
        result = dialog.run()
        dialog.destroy()
        return result == Gtk.ResponseType.OK

    def refresh(self):
        try:
            current, saved, pending = self.backend.status()
            config = self.backend.config()
            for slot in ("A", "B"):
                self.account_labels[slot].set_text("状态待恢复" if pending else "当前使用" if current == slot else "已有存档" if saved[slot] else "尚未归档")
            if pending:
                notice = "上次切换未完成，已停用切换。请先检查并恢复现有资料，不会自动重试。"
            elif current not in ("A", "B", "none"):
                notice = "当前账号标记异常，已停用切换，请先检查资料。"
            else:
                notice = "当前账号：" + (current if current != "none" else "尚未关联")
            mode = "已安装核心" if self.backend.installed.is_file() else "未完整安装 · 使用仓库默认配置"
            self.notice.set_text(notice + "\n" + mode + " · 目标国家：" + config.get("COUNTRY_TARGET", "未知"))
            for slot, button in zip(("A", "B"), self.switch_buttons):
                button.set_sensitive(not self.busy and not pending and current in ("none", "A", "B") and slot != current)
            self.fix_button.set_sensitive(not self.busy and self.backend.installed.is_file())
            self.fix_button.set_tooltip_text("需要先完成 Linux 核心安装；修复会修改代理规则、时区、IPv6 和后台服务")
        except (OSError, UnicodeError) as exc:
            self.notice.set_text("读取状态失败：" + str(exc))
            for button in self.switch_buttons + [self.fix_button]:
                button.set_sensitive(False)

    def switch(self, target):
        if self.busy:
            return
        if self.message("切换到账号 " + target + "？", "会更换整个 Chrome/Chromium 配置目录（包括所有网站登录态、历史和设置）。\n\n请先完全退出浏览器及后台进程。当前资料会先存档；不会自动关闭或重开浏览器。\n\n若操作被中断，将保留恢复资料并阻止再次切换。", confirm=True):
            self.start("switch", target)

    def start(self, action, target=None):
        if self.busy:
            return
        self.busy = True
        self.check_button.set_sensitive(False)
        self.refresh_button.set_sensitive(False)
        self.refresh()
        self.spinner.start()
        if action == "checkdetail":
            self.model.clear()
            for name in CHECK_NAMES:
                self.model.append([name, "检查中", "等待结果…", "#667582"])
            self.summary.set_text("正在检查网络和系统环境…")
        else:
            self.summary.set_text("账号操作后请重新检查环境")
        self.footer.set_text("正在安全检查，最长约两分钟…" if action == "checkdetail" else "正在切换，请保持窗口打开，等待结果…")

        def work():
            try:
                code, output = self.backend.run(action, target)
            except Exception as exc:
                code, output = -1, str(exc)
            GLib.idle_add(self.finished, action, code, output)
        threading.Thread(target=work, daemon=False).start()

    def finished(self, action, code, output):
        self.busy = False
        self.spinner.stop()
        self.check_button.set_sensitive(True)
        self.refresh_button.set_sensitive(True)
        self.refresh()
        if action == "checkdetail":
            rows = parse_checks(output)
            self.model.clear()
            for row in rows:
                self.model.append([row.name, {"ok": "通过", "warn": "待确认", "fail": "未通过"}[row.state], row.detail or "—", {"ok": "#237755", "warn": "#92610a", "fail": "#b63e3e"}[row.state]])
            if code != 0:
                self.summary.set_text("检查未完成，不能确认安全状态")
                self.footer.set_text(output[-700:] or "检查进程异常退出")
            else:
                counts = {state: sum(row.state == state for row in rows) for state in ("ok", "warn", "fail")}
                self.summary.set_text("通过 {ok} 项 · 待确认 {warn} 项 · 未通过 {fail} 项".format(**counts))
                self.footer.set_text("本次检查：" + GLib.DateTime.new_now_local().format("%H:%M:%S") + " · 检查结果不能证明账号可用。")
        else:
            success = code == 0 and ("OK:" in output or "INFO:当前已是账号" in output)
            self.footer.set_text("切换完成，请手动重新打开浏览器。" if success else "切换未完成；请查看提示，未自动重试。")
            self.message("切换完成" if success else "切换未完成", output[-1200:] or "操作失败，资料状态需要人工确认。")
        return False

    def fix(self, *_):
        if self.busy:
            return
        terminal = shutil.which("gnome-terminal")
        if not terminal:
            self.message("未找到 GNOME Terminal", "请在终端手动运行 bash ~/.claude-switcher/claude-switch.sh fix。")
            return
        if self.message("在终端中执行修复？", "修复可能修改代理规则、系统时区、IPv6，并启用会终止 Claude CLI 的后台服务。\n\n需要管理员权限时，请在终端输入 sudo 密码。完成后回到此窗口重新检查。", confirm=True):
            subprocess.Popen([terminal, "--wait", "--title=claude检测器 · 修复", "--", "/bin/bash", "-c", 'bash "$1" fix; printf "\\n操作结束，按回车关闭…"; read -r _', "claude-switcher", str(self.backend.installed)])
            self.footer.set_text("已打开修复终端；尚未验证修复结果，结束后请重新检查。")

    def on_close(self, *_):
        if self.busy:
            self.message("操作仍在进行", "请等待结果后关闭窗口，避免中断正在进行的资料操作。")
            return True
        return False


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.yoursjeyo.ClaudeSwitcher", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        if not self.get_windows():
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            Window(self).show_all()
        window = self.get_windows()[0]
        window.deiconify()
        surface = window.get_window()
        timestamp = Gtk.get_current_event_time()
        if isinstance(surface, GdkX11.X11Window):
            surface.move_to_current_desktop()
            timestamp = GdkX11.x11_get_server_time(surface)
        window.present_with_time(timestamp)


if __name__ == "__main__":
    raise SystemExit(Application().run(None))
