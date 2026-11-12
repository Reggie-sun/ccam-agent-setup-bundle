#!/usr/bin/python3
"""Install desktop entries; optionally configure user-local core and bodyguard."""
from pathlib import Path
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import urllib.request


def configure(root, home, proxy_mode):
    """Explicit opt-in setup. No proxy-rule, timezone or IPv6 mutations."""
    def get_json(url):
        with urllib.request.urlopen(url, timeout=12) as response:
            return json.load(response)
    info = get_json("https://ipinfo.io/json")
    country, timezone = info.get("country", ""), info.get("timezone", "")
    if not re.fullmatch(r"[A-Z]{2}", country) or country == "CN":
        raise RuntimeError("出口国家未知或为 CN，拒绝配置")
    with urllib.request.urlopen("https://www.cloudflare.com/cdn-cgi/trace", timeout=12) as response:
        trace = dict(line.split("=", 1) for line in response.read().decode().splitlines() if "=" in line)
    if trace.get("loc") != country:
        raise RuntimeError("两个出口国家检测不一致，拒绝配置")
    if not re.fullmatch(r"[A-Za-z_+-]+(?:/[A-Za-z_+-]+)+", timezone):
        raise RuntimeError("出口时区无效，拒绝配置")
    # Keep an existing system timezone belonging to the verified exit country.
    # A US egress city changing must not force New York users to Los Angeles.
    zone_table = Path("/usr/share/zoneinfo/zone.tab")
    current = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"], capture_output=True, text=True, check=True).stdout.strip()
    if zone_table.is_file():
        zones = [line.split() for line in zone_table.read_text().splitlines() if line and not line.startswith("#")]
        if any(len(row) >= 3 and country in row[0].split(",") and current == row[2] for row in zones):
            timezone = current
    regions = {"US": "en_US", "JP": "ja_JP", "TW": "zh_TW", "HK": "zh_HK", "SG": "en_SG", "GB": "en_GB", "DE": "de_DE", "FR": "fr_FR", "KR": "ko_KR", "AU": "en_AU", "CA": "en_CA"}
    region = regions.get(country)
    if not region:
        raise RuntimeError("未收录此出口地区，请先手动配置核心")
    base = home / ".claude-switcher"
    base.mkdir(mode=0o700, exist_ok=True)
    locales = base / "locales"
    locales.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(["localedef", "--no-archive", "-i", region, "-f", "UTF-8", str(locales / (region + ".UTF-8"))], check=True)
    values = {"PROXY_MODE": proxy_mode, "PROXY_GROUP": "PROXY", "COUNTRY_TARGET": country, "TZ_TARGET": timezone, "LOCALE_TARGET": region}
    text = (root / "linux/claude-switch.sh").read_text()
    for key, value in values.items():
        text = re.sub(r'^' + key + r'="[^"\n]*"', lambda _: key + '="' + value + '"', text, flags=re.MULTILINE)
    guard = (root / "linux/claude-bodyguard.sh").read_text().replace("__PROXY_MODE__", proxy_mode)
    unit = home / ".config/systemd/user/claude-bodyguard.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit_text = '[Unit]\nDescription=Claude Bodyguard (current user CLI only)\n\n[Service]\nExecStart=/bin/bash "' + str(base / "claude-bodyguard.sh") + '"\nRestart=always\nRestartSec=5\nNoNewPrivileges=yes\n\n[Install]\nWantedBy=default.target\n'
    outputs = {base / "claude-switch.sh": text, base / "claude-bodyguard.sh": guard, unit: unit_text}
    backup = base / "setup-backups" / datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    for path, content in outputs.items():
        if path.exists():
            backup.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(path, backup / path.name)
        temp = path.with_name(path.name + ".setup-new")
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(content)
        temp.chmod(0o700 if path.suffix == ".sh" else 0o600)
        temp.replace(path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "claude-bodyguard.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "claude-bodyguard.service"], check=True)
    print("已配置用户核心、应用地区和当前用户 CLI 保镖；未修改系统语言、时区、IPv6 或代理规则。")


def install():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true", help="按已核验出口配置用户核心、应用地区和保镖服务")
    parser.add_argument("--proxy-mode", choices=("clash-verge", "mihomo", "sing-box", "auto"), default="auto")
    args = parser.parse_args()
    import gi
    gi.require_version("Gtk", "3.0")
    root = Path(__file__).resolve().parents[1]
    home = Path.home()
    if args.configure:
        configure(root, home, args.proxy_mode)
    desktop = home / "Desktop"
    if shutil.which("xdg-user-dir"):
        result = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=True)
        desktop = Path(result.stdout.strip())
    if not desktop.is_absolute() or desktop == home:
        raise RuntimeError("未找到独立桌面目录，请先配置 xdg-user-dirs")

    def quoted(path):
        value = str(path).replace("%", "%%")
        for char in ("\\", '"', '`', '$'):
            value = value.replace(char, "\\" + char)
        return '"' + value + '"'

    command = "/usr/bin/python3 " + quoted(root / "linux/desktop.py")
    core = home / ".claude-switcher/claude-switch.sh"
    if core.is_file():
        match = re.search(r'^LOCALE_TARGET="([a-z]{2}_[A-Z]{2})"', core.read_text(), re.MULTILINE)
        if match and (home / ".claude-switcher/locales" / (match[1] + ".UTF-8")).is_dir():
            command = "/usr/bin/env " + quoted("LOCPATH=" + str(home / ".claude-switcher/locales")) + " " + quoted("LANG=" + match[1] + ".UTF-8") + " " + quoted("LC_ALL=" + match[1] + ".UTF-8") + " " + command
    entry = "\n".join([
        "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=claude检测器",
        "Comment=Claude 账号管理与环境检查 · Linux 桌面版",
        "Exec=" + command,
        "TryExec=/usr/bin/python3", "Icon=" + str(root / "assets/icon-2048.png"),
        "Terminal=false", "Categories=Utility;", "StartupNotify=true", "",
    ])
    apps = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share"))) / "applications"
    for target in (apps / "claude-switcher.desktop", desktop / "Claude切换器.desktop"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry, encoding="utf-8")
        target.chmod(0o755)
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(target)], check=True)
        print("已创建：", target)
    base = home / ".claude-switcher"
    base.mkdir(mode=0o700, exist_ok=True)
    probe = base / "stun-probe.py"
    if not probe.exists() and not probe.is_symlink():
        probe.symlink_to(root / "linux/stun-probe.py")
    if shutil.which("gio"):
        subprocess.run(["gio", "set", str(desktop / "Claude切换器.desktop"), "metadata::trusted", "true"], check=False)
    print("双击桌面 claude检测器 启动。" + ("保镖服务已启用。" if args.configure else "未修改代理、时区、IPv6 或后台服务。"))


if __name__ == "__main__":
    install()
