#!/usr/bin/python3
"""Independent Claude terminals sharing one persistent private browser service."""
from pathlib import Path
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import time

source = importlib.util.spec_from_file_location("browser_environment", Path(__file__).with_name("browser-only.py"))
core = importlib.util.module_from_spec(source)
source.loader.exec_module(core)


def process_start(pid):
    text = Path(f"/proc/{pid}/stat").read_text()
    return text[text.rfind(")") + 2:].split()[19]


def service_info(m):
    try:
        data = json.loads((Path(m["base"]) / "config/service.json").read_text())
        pid = data["pid"]
        if type(pid) is not int or pid <= 0 or Path(f"/proc/{pid}").stat().st_uid != os.getuid():
            return None
        if process_start(pid) != data["start"]:
            return None
        actual = {name: os.readlink(f"/proc/{pid}/ns/{name}") for name in ("user", "mnt", "net", "pid")}
        if actual != data["namespaces"] or any(actual[name] == host for name, host in m["host_namespaces"].items()):
            return None
        return data
    except (OSError, KeyError, ValueError):
        return None


def ensure_service(m):
    data = service_info(m)
    if data:
        return data
    base = Path(m["base"])
    with (base / "state/browser-start.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = service_info(m)
        if data:
            return data
        # A user service survives closing any CLI terminal and owns all browser
        # descendants. It receives display settings, never CLI credentials.
        manager_env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
                       "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus"}
        unit = "claude-private-browser.service"
        state = subprocess.run(["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"],
                               env=manager_env, capture_output=True, text=True).stdout.strip()
        if state not in ("active", "activating", "reloading"):
            log = str(base / "state/browser-service.log")
            command = ["systemd-run", "--user", "--quiet", "--collect", "--service-type=exec", "--unit=" + unit,
                       "--property=StandardOutput=append:" + log, "--property=StandardError=append:" + log]
            for key in ("DISPLAY", "XAUTHORITY"):
                if key in os.environ:
                    command += ["--setenv=" + key + "=" + os.environ[key]]
            command += ["/usr/bin/aa-exec", "-p", "claude-browser-only", "--", "/usr/bin/python3",
                        str(base / "code/browser-only.py"), "serve"]
            if subprocess.run(command, env=manager_env, capture_output=True).returncode:
                raise RuntimeError("专用浏览器用户服务未启动；未绕过隔离。")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            data = service_info(m)
            if data:
                return data
            status = subprocess.run(["systemctl", "--user", "is-active", unit], env=manager_env, capture_output=True)
            if status.returncode:
                raise RuntimeError("专用浏览器服务未启动；没有启动其他浏览器。")
            time.sleep(.15)
        raise RuntimeError("专用浏览器仍在启动；稍后重试，未绕过隔离。")


def client(m, values):
    values = core.cli_arguments(values)
    data = ensure_service(m)
    m = {**m, **core.project_access(m, Path.cwd())}
    # Browser lives in a sibling PID namespace. Each terminal gets its own
    # filesystem/PID sandbox and its own workspace while sharing its network.
    env = core.clean_environment(m, {})
    env.update(data["proxy_env"])
    env.update({"CLAUDE_BROWSER_ONLY_WS": data["ws"], "CLAUDE_BROWSER_ONLY_ACTIVE": "1",
                "CLAUDE_BROWSER_ONLY_ARGUMENTS": json.dumps(values), "CLAUDE_WORKSPACES": json.dumps(m["workspaces"])})
    command = core.outer_command(m, env, "inside-client")
    # The SDK network's owning user namespace maps our host UID to zero.
    # Remap it back to our ordinary UID in each client; Claude must never run
    # as namespace-root (and rejects permission bypass when getuid() is zero).
    command[1:1] = ["--uid", str(os.getuid()), "--gid", str(os.getgid())]
    tail = command.index("--")
    command[tail + 1:] = ["/usr/bin/python3", str(Path(m["base"]) / "code/browser-only.py"), "inside-client"]
    net = os.open(f'/proc/{data["pid"]}/ns/net', os.O_RDONLY)
    owner = fcntl.ioctl(net, 0xb701)  # NS_GET_USERNS: enter the network's owner.
    try:
        args = ["/usr/bin/nsenter", f"--user=/proc/self/fd/{owner}", "--preserve-credentials",
                f'--net=/proc/{data["pid"]}/ns/net', "--", *command]
        # No API keys or proxy secrets appear in command-line arguments.
        return subprocess.call(args, env=env, pass_fds=(owner,), cwd=Path(m["base"]) / "state/workspace")
    finally:
        os.close(owner)
        os.close(net)


if __name__ == "__main__":
    try:
        m = json.loads((core.base_path() / "config/manifest.json").read_text())
        raise SystemExit(client(m, sys.argv[1:]))
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else "Claude 会话启动失败；未绕过浏览器隔离。", file=sys.stderr)
        raise SystemExit(1)
