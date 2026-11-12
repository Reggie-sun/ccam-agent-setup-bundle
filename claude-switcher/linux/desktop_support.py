"""Small, GTK-independent adapters for the existing Linux core."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import signal
import subprocess

DIRECTORY = Path(__file__).resolve().parent
CHECK_NAMES = (
    "代理内核", "TUN网卡", "出口IP", "IP一致", "时区", "地区", "IPv6",
    "DNS", "WebRTC", "保镖", "规则置顶", "客户端语言", "ASN属性", "生态可达性",
)


def private_text(text):
    text = re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", "[IP已隐藏]", text)
    return re.sub(r"(?<!\w)(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]*(?!\w)", "[IP已隐藏]", text)


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str


def parse_checks(output):
    rows = {}
    for line in output.splitlines():
        match = re.fullmatch(r"([^=]+)=(ok|warn|fail)(?::(.*))?", line)
        if not match or match[1] not in CHECK_NAMES:
            continue
        name, state, detail = match[1], match[2], match[3] or ""
        if name in rows:
            state, detail = "fail", "检查结果重复，不能确认状态"
        elif name == "WebRTC" and ("零暴露" in detail or "未收到STUN" in detail):
            state, detail = "warn", "未收到 STUN 响应，无法证明零暴露"
        elif name == "生态可达性" and state == "ok":
            state = "warn"
            detail += "；仅代表 HTTP 有响应，不能证明账号可用"
        rows[name] = Check(name, state, private_text(detail))
    return [rows.get(name, Check(name, "fail", "未返回检查结果")) for name in CHECK_NAMES]


class Backend:
    def __init__(self, home=None):
        self.home = Path(home) if home else Path.home()
        self.base = self.home / ".claude-switcher"
        self.core = DIRECTORY / "claude-switch.sh"
        self.installed = self.base / "claude-switch.sh"

    @property
    def check_core(self):
        return self.installed if self.installed.is_file() else self.core

    def config(self):
        text = self.check_core.read_text(encoding="utf-8")
        return dict(re.findall(r'^([A-Z_]+)="([^"\n]*)"', text, re.MULTILINE))

    def status(self):
        current = self.base / "current"
        account = current.read_text().strip() if current.is_file() else "none"
        pending = [self.base / "switch.pending", self.base / "current.new"]
        for name in ("google-chrome", "chromium"):
            pending += [self.home / ".config" / (name + suffix) for suffix in (".new", ".previous")]
        for slot in ("A", "B"):
            pending += [self.base / "accounts" / (slot + suffix) for suffix in (".new", ".previous", ".tmp")]
        return account, {slot: (self.base / "accounts" / slot).is_dir() for slot in ("A", "B")}, any(p.exists() or p.is_symlink() for p in pending)

    def command(self, action, target=None):
        if action == "checkdetail":
            return ["/bin/bash", str(self.check_core), action]
        if action == "switch" and target in ("A", "B"):
            # Always use the reviewed repository switch, even with an older install.
            return ["/bin/bash", str(self.core), action, target]
        raise ValueError("不支持的操作")

    def run(self, action, target=None):
        env = {**os.environ, "HOME": str(self.home)}
        process = subprocess.Popen(
            self.command(action, target), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            # Never terminate an account transaction just because it takes time.
            output, _ = process.communicate(timeout=120 if action == "checkdetail" else None)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RuntimeError("检查超过两分钟，结果未知；未自动重试") from None
        return process.returncode, private_text(output.decode("utf-8", errors="replace"))
