#!/usr/bin/python3
"""Choose one registered Chrome; never infer an account from a default browser."""
from pathlib import Path
import importlib.util
import json
import os
import sys
import time
import urllib.request


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def endpoint(root, candidates):
    try:
        lines = (root / "DevToolsActivePort").read_text().splitlines()
        port = int(lines[0]); route = lines[1]
        if not 1 <= port <= 65535 or not route.startswith("/devtools/browser/"):
            return None
        # A stale port file cannot select a different account after port reuse.
        inodes = set()
        for table in ("tcp", "tcp6"):
            for line in Path("/proc/net", table).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A" and fields[1] in (f"0100007F:{port:04X}", f"00000000000000000000000001000000:{port:04X}"):
                    inodes.add("socket:[" + fields[9] + "]")
        if not inodes or not any(any(os.readlink(fd) in inodes for fd in Path(f"/proc/{pid}/fd").iterdir()) for pid, _ in candidates):
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            data = json.load(response)
        expected = f"ws://127.0.0.1:{port}" + route
        return expected if data.get("webSocketDebuggerUrl") == expected else None
    except (OSError, ValueError, IndexError, KeyError):
        return None


def main(values):
    base = Path(__file__).resolve().parent.parent
    manifest = json.loads((base / "config/manifest.json").read_text())
    home = Path(manifest["host_home"])
    os.environ["HOME"] = str(home)
    owner = home / ".local/share/chrome-profile-grouping"
    manager = load("chrome_manager", owner / "manager.py").Manager()
    region = load("chrome_region", owner / "region.py")
    registry = manager.read_registry()["profiles"]
    profiles = []
    for key, item in registry.items():
        root = manager.profile_root(key)
        if not root.is_dir() or root.is_symlink():
            continue
        live = region.processes(root)
        profiles.append({"id": key, "name": item["name"], "directory": item["directory"],
                         "running": bool(live), "debug_ready": bool(endpoint(root, live))})
    if values == ["list"]:
        return {"profiles": profiles}
    if len(values) != 2 or values[0] != "select" or values[1] not in {p["id"] for p in profiles}:
        raise RuntimeError("请先列出浏览器，再按 profile_id 选择目标。")
    key = values[1]; root = manager.profile_root(key)
    region.require_us(region.settings(owner))
    live = region.processes(root)
    if not live:
        manager.launch(key)
    elif not any(b"--proxy-server=http://127.0.0.1:17987" in args for _, args in live):
        raise RuntimeError("该浏览器尚未应用美国设置；请保存页面工作并关闭、重新打开此浏览器后再选择。")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        live = region.processes(root)
        target = endpoint(root, live)
        if target:
            return {"profile_id": key, "name": registry[key]["name"], "wsEndpoint": target}
        if live and not any(any(arg.startswith(b"--remote-debugging-port=") for arg in args) for _, args in live):
            raise RuntimeError("该浏览器未启用自动化接口；保存工作并重新打开此浏览器后再试。")
        time.sleep(.15)
    raise RuntimeError("所选浏览器未就绪，未连接其他浏览器。")


if __name__ == "__main__":
    try:
        print(json.dumps(main(sys.argv[1:]), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, RuntimeError) else "浏览器选择失败，未切换到其他账号。"}, ensure_ascii=False))
        raise SystemExit(1)
