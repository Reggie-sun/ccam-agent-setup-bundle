#!/usr/bin/python3
"""Shared launch policy for the existing Chrome profile manager."""
from pathlib import Path
import json
import os
import tempfile
import urllib.request


def settings(base):
    file = Path(base) / "browser-policy.json"
    if not file.exists():
        return None
    value = json.loads(file.read_text())
    if (not isinstance(value, dict)
            or value.get("timezone") not in ("America/New_York", "America/Los_Angeles")
            or value != {"version": 1, "country": "US", "timezone": value.get("timezone"),
                 "ui_language": "zh-CN", "accept_languages": "en-US,en",
                 "proxy_url": "http://127.0.0.1:17987"}):
        raise RuntimeError("Chrome 地区配置异常，保留浏览器资料并停止启动。")
    return value


def require_us(policy):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": policy["proxy_url"]}))
    with opener.open("https://www.cloudflare.com/cdn-cgi/trace", timeout=12) as response:
        trace = dict(line.split("=", 1) for line in response.read().decode().splitlines() if "=" in line)
    if trace.get("loc") != "US":
        raise RuntimeError("Chrome 的美国专用出口不可用，未回退到国内直连。")


def processes(root):
    expected = ("--user-data-dir=" + str(Path(root).resolve())).encode()
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            if (proc / "comm").read_text().strip() not in ("chrome", "google-chrome"):
                continue
            args = (proc / "cmdline").read_bytes().split(b"\0")
            if expected in args and not any(arg.startswith(b"--type=") for arg in args):
                exe = (proc / "exe").resolve(strict=True)
                if exe.name in ("chrome", "google-chrome"):
                    found.append((int(proc.name), args))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError) as error:
            raise RuntimeError("无法核验 Chrome 进程，保留浏览器资料并停止修改。") from error
    return found


def preferences(root, directory):
    try:
        running = processes(root)
    except RuntimeError:
        return False
    if running:
        return False  # Chrome owns live Preferences; apply at its next launch.
    file = Path(root) / directory / "Preferences"
    file.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(file.read_text()) if file.exists() else {}
    data.setdefault("intl", {})["accept_languages"] = "en-US,en"
    data.setdefault("spellcheck", {})["dictionaries"] = ["en-US"]
    data.setdefault("profile", {}).setdefault("default_content_setting_values", {})["geolocation"] = 2
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=file.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(data, output, ensure_ascii=False)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, file)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return True


def arguments(base, root, directory):
    policy = settings(base)
    if not policy:
        return []
    require_us(policy)
    preferences(root, directory)
    return ["--lang=zh-CN", "--accept-lang=en-US,en", "--proxy-server=" + policy["proxy_url"],
            "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]


def environment(base):
    policy = settings(base)
    return {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "LANGUAGE": "zh_CN:zh", "TZ": policy["timezone"]} if policy else {}
