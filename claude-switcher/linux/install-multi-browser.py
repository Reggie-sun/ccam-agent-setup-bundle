#!/usr/bin/python3
"""Enable Chinese Chrome UI, US browser routing, and explicit automation choice."""
from pathlib import Path
import ast
import datetime
import importlib.util
import json
import shutil
import subprocess


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def manager_source(text):
    if "browser_region.arguments(self.base" in text:
        return text
    anchors = ("import argparse\n", 'ZENITY_BIN = "/usr/bin/zenity"',
               '            value = json.loads(data)\n', '        if new_window:\n',
               '            self.launch_argv(profile_id, urls, new_window=new_window, incognito=incognito),\n')
    if any(text.count(anchor) != 1 for anchor in anchors):
        raise RuntimeError("Chrome manager source changed; preserved for reconciliation")
    text = text.replace(anchors[0], anchors[0] + "import importlib.util\n", 1)
    loader = 'spec = importlib.util.spec_from_file_location("browser_region", Path(__file__).with_name("region.py"))\nbrowser_region = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(browser_region)\n\n'
    text = text.replace(anchors[1], loader + anchors[1], 1)
    text = text.replace(anchors[2], anchors[2] + '            if value == {"version": 2, "profiles": "*"}:\n                return True\n', 1)
    text = text.replace(anchors[3], '        argv.extend(browser_region.arguments(self.base, self.profile_root(profile_id), directory))\n' + anchors[3], 1)
    text = text.replace(anchors[4], anchors[4] + '            env={**os.environ, **browser_region.environment(self.base)},\n', 1)
    ast.parse(text)
    return text


def install(home):
    source = Path(__file__).resolve().parent
    base = home / ".local/share/claude-browser-only"
    owner = home / ".local/share/chrome-profile-grouping"
    manager = owner / "manager.py"
    original = manager.read_text()
    updated = manager_source(original)
    manifest = json.loads((base / "config/manifest.json").read_text())
    if manifest["host_home"] != str(home) or not Path(manifest["cli"]).is_file():
        raise RuntimeError("Existing native Claude installation must be reconciled first")
    backup = owner / "backups" / ("multi-browser-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
    backup.mkdir(mode=0o700, parents=True)
    for index, file in enumerate((manager, owner / "cdp-profiles.json", owner / "browser-policy.json", home / ".local/bin/claude", home / ".local/bin/claude-browser")):
        if file.is_file():
            shutil.copyfile(file, backup / (str(index) + "-" + file.name)); (backup / (str(index) + "-" + file.name)).chmod(0o600)
    for name in ("claude-host.py", "chrome-browsers.py", "chrome-router.mjs"):
        shutil.copyfile(source / name, base / "code" / name); (base / "code" / name).chmod(0o700)
    shutil.copyfile(source / "chrome-region.py", owner / "region.py")
    writer = load("default_claude", source / "default-claude.py")
    writer.atomic_text(manager, updated, 0o700)
    region = load("chrome_region", owner / "region.py")
    policy = region.settings(owner) or {"version": 1, "country": "US", "timezone": "America/New_York", "ui_language": "zh-CN",
                                      "accept_languages": "en-US,en", "proxy_url": "http://127.0.0.1:17987"}
    region.require_us(policy)
    writer.atomic_text(owner / "browser-policy.json", json.dumps(policy, indent=2), 0o600)
    writer.atomic_text(owner / "cdp-profiles.json", json.dumps({"version": 2, "profiles": "*"}), 0o600)
    config = {"mcpServers": {"chrome-browsers": {"type": "stdio", "command": manifest["node"], "args": [str(base / "code/chrome-router.mjs")]}}}
    writer.atomic_text(base / "config/multi-browser-mcp.json", json.dumps(config, indent=2), 0o600)
    # CLAUDE_CONFIG_DIR keeps the same credentials, plugins and history while
    # restoring the real HOME for browser/file tools. Preserve the old file.
    old_config = base / "state/home/.claude.json"
    new_config = base / "state/home/.claude/.claude.json"
    if not new_config.exists():
        shutil.copyfile(old_config, new_config); new_config.chmod(0o600)
    registry = json.loads((owner / "registry.json").read_text())
    applied, pending = [], []
    for key, item in registry["profiles"].items():
        root = home / ".config/chrome-profiles" / key
        if root.is_symlink() or not root.is_dir():
            continue
        file = root / item["directory"] / "Preferences"
        if file.exists():
            shutil.copyfile(file, backup / (key + "-Preferences")); (backup / (key + "-Preferences")).chmod(0o600)
        (applied if region.preferences(root, item["directory"]) else pending).append(key)
    private = home / ".local/bin/claude-browser"
    writer.atomic_text(private, '#!/bin/sh\nexec /usr/bin/python3 "' + str(manager) + '" launch cp-a1f58b023841 "$@"\n', 0o700)
    browser_entry = home / ".local/share/applications/claude-private-browser.desktop"
    if browser_entry.exists():
        shutil.copyfile(browser_entry, backup / browser_entry.name)
        text = browser_entry.read_text().replace("Name=Claude 专用浏览器", "Name=独立 Chrome 浏览器")
        text = text.replace("Comment=独立浏览器资料 · 启动前检查代理出口 · 禁用同步和扩展", "Comment=中文界面 · 美国地区 · 可单独选择自动化")
        writer.atomic_text(browser_entry, text, 0o755)
    writer.install_default(home, base)
    desktop = home / ".local/share/applications/claude-browser-only.desktop"
    content = '[Desktop Entry]\nType=Application\nName=Claude Code\nComment=正常开发工具；按需选择 Chrome 自动化\nTerminal=false\nExec=/usr/bin/gnome-terminal --wait --title="Claude Code" -- ' + str(home / ".local/bin/claude") + '\nIcon=' + str(source.parent / "assets/icon-2048.png") + '\nCategories=Development;\n'
    writer.atomic_text(desktop, content, 0o755)
    desk = Path(subprocess.check_output(["xdg-user-dir", "DESKTOP"], text=True).strip())
    if desk.is_absolute() and desk != home:
        shutil.copyfile(desktop, desk / "Claude浏览器专用助手.desktop")
    return {"preferences_applied": len(applied), "running_profiles_apply_on_reopen": len(pending), "backup": str(backup)}


if __name__ == "__main__":
    print(json.dumps(install(Path.home()), ensure_ascii=False))
