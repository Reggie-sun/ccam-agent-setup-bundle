#!/usr/bin/python3
"""A normal Claude development session with isolated private-browser access."""
from pathlib import Path
import argparse
import base64
import fcntl
import json
import os
import re
import secrets
import select
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

SRT_VERSION = "0.0.81"
MCP_VERSION = "1.12.0"
AUTH_KEYS = {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "API_TIMEOUT_MS"}


def base_path():
    file = Path(__file__).resolve()
    return file.parent.parent if file.parent.name == "code" else Path.home() / ".local/share/claude-browser-only"


def resolve_installation(base):
    manifest = Path(base) / "config/manifest.json"
    if manifest.exists():
        prefix = Path(json.loads(manifest.read_text())["node"]).parent.parent
        package = prefix / "lib/node_modules/@anthropic-ai/claude-code"
    else:
        executable = shutil.which("claude")
        if not executable:
            raise RuntimeError("Claude Code npm installation is missing")
        resolved = Path(executable).resolve()
        package = next((p for p in resolved.parents if p.name == "claude-code" and (p / "package.json").is_file()), None)
        if package is None:
            raise RuntimeError("Original npm Claude Code package is unavailable")
        prefix = package.parents[3]
    metadata = json.loads((package / "package.json").read_text())
    if metadata.get("name") != "@anthropic-ai/claude-code":
        raise RuntimeError("Unexpected CLI package")
    cli = (package / metadata["bin"]["claude"]).resolve()
    node = prefix / "bin/node"
    if not cli.is_relative_to(package.resolve()) or not cli.is_file() or not node.is_file():
        raise RuntimeError("Original CLI backend is unavailable; refusing recursive launcher")
    with cli.open("rb") as source:
        native = source.read(4) == b"\x7fELF"
    if cli.suffix != ".js" and not (native and os.access(cli, os.X_OK)):
        raise RuntimeError("Unsupported CLI backend format")
    return cli, node


def default_request(values):
    # Older interactive shells added this flag automatically. The protected
    # policy supplies its own browser permissions and never forwards the flag.
    values = [value for value in values if value not in ("--dangerously-skip-permissions", "--yolo")]
    if not values:
        return "run"
    if values in (["--help"], ["-h"]):
        return "inside-help"
    if values in (["--version"], ["-v"]):
        return "inside-version"
    if values in (["auth", "status"], ["auth", "status", "--json"]):
        return "inside-status"
    cli_arguments(values)
    return "run"


def cli_arguments(values):
    values = [value for value in values if value not in ("--dangerously-skip-permissions", "--yolo")]
    options = values[:values.index("--")] if "--" in values else values
    if any(value == "--chrome" or value == "--mcp-config" or value.startswith("--mcp-config=") for value in options):
        raise RuntimeError("浏览器连接由专用环境固定管理；其他开发工具和普通 CLI 参数可正常使用。")
    return values


def make_policy(m):
    base = Path(m["base"])
    return {
        "network": {"allowedDomains": [], "deniedDomains": ["localhost", "127.0.0.1", "[::1]"],
                    "deniedResolvedAddresses": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"],
                    "allowAllUnixSockets": True, "allowLocalBinding": False},
        "filesystem": {
            # The outer allow-only mount namespace owns read isolation. Do not
            # restore a broad host-home ancestor while re-allowing nested paths.
            "denyRead": [], "allowRead": [],
            "allowWrite": [str(base / "state"), m["profile_root"], "/tmp", *m.get("workspaces", [])], "denyWrite": [],
        },
        "allowNestedUserNamespaces": True, "enableWeakerNestedSandbox": False,
        "enableWeakerNetworkIsolation": False,
    }


def outer_command(m, env, mode):
    """Build an empty-root filesystem, never bind the host root/home/run/tmp."""
    base = Path(m["base"])
    args = ["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--new-session", "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run"]
    for target in ("/usr", "/lib", "/lib64", "/opt/google/chrome"):
        if Path(target).exists():
            args += ["--ro-bind", str(Path(target).resolve()), target]
    args += ["--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin"]
    for target in ("/etc/ssl", "/etc/fonts", "/etc/alternatives", "/etc/ca-certificates", "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
                   "/etc/nsswitch.conf", "/etc/resolv.conf", "/etc/hosts", "/etc/passwd", "/etc/group", "/etc/localtime", "/etc/gai.conf"):
        if Path(target).exists():
            args += ["--ro-bind", str(Path(target).resolve()), target]
    for target in (base / "code", base / "config", base / "runtime", Path(m["node_prefix"])):
        args += ["--ro-bind", str(target), str(target)]
    # GUI-aware xdg-open can choose a desktop handler before consulting BROWSER.
    # Replace even its absolute path only inside this mount namespace.
    args += ["--ro-bind", str(base / "code/bin/xdg-open"), "/usr/bin/xdg-open"]
    for target in (base / "state", base / "state/workspace", Path(m["profile_root"])):
        args += ["--bind", str(target), str(target)]
    for target in m.get("workspaces", []):
        args += ["--bind", target, target]
    for target in m.get("hidden_browser_roots", []):
        args += ["--tmpfs", target]
    if m.get("display_socket"):
        args += ["--ro-bind", m["display_socket"], "/tmp/.X11-unix/X" + m["display_number"]]
    # The caller supplies a freshly constructed subprocess environment. Never
    # put API credentials into --setenv command-line arguments.
    args += ["--chdir", m.get("working_directory", str(base / "state/workspace"))]
    args += ["--", m["node"], str(base / "code/browser-only-runtime.mjs"), "sandbox", mode]
    return args


def claude_command(m):
    config = Path(m["base"]) / "config"
    program = [m["node"], m["cli"]] if Path(m["cli"]).suffix == ".js" else [m["cli"], "--no-chrome"]
    return program + ["--dangerously-skip-permissions", "--append-system-prompt", "Browser automation must use the claude-browser MCP and its dedicated browser. Do not access other browser profiles or debugging endpoints. All normal development tools remain available.", "--mcp-config", str(config / "mcp.json"),
            "--settings", str(config / "settings.json")]


def client_command(m):
    # Writable project mounts can contain host filesystem Unix sockets, which
    # bypass network namespaces. Use the pinned SDK's kernel filter, inherited
    # by Bash/plugins/MCP. socketpair IPC and private-network TCP remain usable.
    architecture = {"x86_64": "x64", "aarch64": "arm64"}.get(os.uname().machine)
    if not architecture:
        raise RuntimeError("Unsupported architecture for mandatory Unix socket isolation")
    helper = Path(m["base"]) / "runtime/node_modules/@anthropic-ai/sandbox-runtime/vendor/seccomp" / architecture / "apply-seccomp"
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise RuntimeError("Required Unix socket isolation helper missing")
    return [str(helper), *claude_command(m)]


def desktop_session_command(base, launcher, restricted_userns):
    if restricted_userns:
        return '/usr/bin/aa-exec -p claude-browser-only -- /usr/bin/python3 "' + str(base / "code/browser-only.py") + '" run'
    return str(launcher)


def mcp_config(m):
    return {"mcpServers": {"claude-browser": {"type": "stdio", "command": m["node"],
            "args": [str(Path(m["base"]) / "code/browser-only-runtime.mjs"), "mcp"]}}}


def clean_environment(m, auth):
    base = Path(m["base"])
    env = {key: value for key, value in auth.items() if key in AUTH_KEYS and isinstance(value, str)}
    env.update({"HOME": str(base / "state/home"), "TMPDIR": str(base / "state/tmp"),
                "PATH": str(base / "code/bin") + ":" + str(Path(m["node"]).parent) + ":/usr/bin:/bin:/usr/sbin",
                "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TZ": "America/New_York", "TERM": os.environ.get("TERM", "xterm-256color"),
                "SHELL": "/bin/bash", "BROWSER": str(base / "code/bin/browser-open"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
                "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1", "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"})
    return env


def validate_link(url):
    if len(url) > 16384 or any(ord(c) < 32 for c in url):
        raise ValueError("Invalid browser link")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Only HTTP/HTTPS browser links are allowed")
    return url


def configure_us_preferences(profile, directory):
    """Update only locale/privacy preferences before starting this profile."""
    path = Path(profile) / directory / "Preferences"
    if path.is_symlink():
        raise RuntimeError("Unsafe browser preferences path")
    current = json.loads(path.read_text()) if path.exists() else {}
    desired = json.loads(json.dumps(current))
    desired.setdefault("intl", {}).update(accept_languages="en-US,en", selected_languages="en-US,en")
    desired.setdefault("spellcheck", {})["dictionaries"] = ["en-US"]
    desired.setdefault("profile", {}).setdefault("default_content_setting_values", {})["geolocation"] = 2
    if current == desired:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(desired, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def project_access(m, cwd):
    home, base = Path(m["host_home"]).resolve(), Path(m["base"]).resolve()
    cwd = Path(cwd).resolve()
    if cwd.is_relative_to(home):
        for ancestor in (cwd, *cwd.parents):
            if ancestor == home:
                break
            if any((ancestor / marker).exists() or (ancestor / marker).is_symlink()
                   for marker in ("Local State", "SingletonLock", "prefs.js")):
                raise RuntimeError("当前目录属于浏览器资料，不能作为开发项目开放。")
    roots = []
    if (home / "vscode_folder").is_dir():
        candidate = (home / "vscode_folder").resolve()
        if not candidate.is_relative_to(home) or candidate == home or candidate.relative_to(home).parts[0].startswith("."):
            raise RuntimeError("项目根目录指向受保护位置，拒绝开放。")
        roots.append(candidate)
    if cwd != home and cwd.is_relative_to(home) and not cwd.is_relative_to(base):
        first = cwd.relative_to(home).parts[0]
        if first.startswith("."):
            raise RuntimeError("请从项目目录启动；其他浏览器和用户控制目录不作为项目挂载。")
        if not any(cwd.is_relative_to(root) for root in roots):
            roots.append(cwd)
    hidden = set()
    for root in roots:
        result = subprocess.run(["/usr/bin/rg", "--files", "--null", "--hidden", "--no-ignore", "-g", "Local State", "-g", "SingletonLock", "-g", "prefs.js", str(root)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode not in (0, 1):
            raise RuntimeError("无法核验项目中的浏览器资料目录，拒绝开放项目。")
        for line in result.stdout.split("\0"):
            if not line:
                continue
            directory = Path(line).parent.resolve()
            if directory.is_relative_to(root):
                hidden.add(str(directory))
    if any(cwd.is_relative_to(Path(root)) for root in hidden):
        raise RuntimeError("当前目录属于浏览器资料，不能作为开发项目开放。")
    working = cwd if any(cwd.is_relative_to(root) for root in roots) else base / "state/workspace"
    return {"workspaces": [str(root) for root in roots], "hidden_browser_roots": sorted(hidden), "working_directory": str(working)}


def local_json(m, path, method="GET"):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f'http://127.0.0.1:{m["port"]}' + path, method=method)
    with opener.open(request, timeout=4) as response:
        return json.load(response)


def require_isolation(m):
    for name, host in m["host_namespaces"].items():
        if os.readlink("/proc/self/ns/" + name) == host:
            raise RuntimeError("Required OS isolation is missing; refusing browser access")


def tool_environment(env):
    return {key: value for key, value in env.items() if key not in AUTH_KEYS}


def tool_container(m, display=None):
    # A fresh PID namespace hides the CLI and its /proc environ/fds. Hide its
    # persistent home too, including any credentials written by /login.
    command = ["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid", "--new-session",
            "--cap-drop", "ALL", "--bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--tmpfs", str(Path(m["base"]) / "state/home"), "--tmpfs", str(Path(m["base"]) / "state/tmp")]
    if display:
        if not re.fullmatch(r":[0-9]{3,5}", display):
            raise RuntimeError("Invalid private browser display")
        target = "/tmp/.X11-unix/X" + display[1:]
        command += ["--ro-bind", target, target]
    return command + ["--"]


def start_display(m):
    """Xephyr sees the host display; its isolated clients see only Xephyr.

    The server also gets an empty-root sandbox so X11 font/keymap requests
    cannot make it read the host's browser files on behalf of a client.
    """
    display = os.environ.get("DISPLAY", "")
    match = re.fullmatch(r"(?:unix/)?(:[0-9]+)(?:\.[0-9]+)?", display)
    authority = Path(os.environ.get("XAUTHORITY", str(Path.home() / ".Xauthority")))
    if not match or not authority.is_file() or not shutil.which("Xephyr") or not shutil.which("xauth"):
        raise RuntimeError("可见浏览器需要本地 X11、Xephyr 和 xauth；请从桌面入口启动。")
    parent_number = match[1][1:]
    parent_socket = "/tmp/.X11-unix/X" + parent_number
    private = tempfile.TemporaryDirectory(prefix="display-", dir=Path(m["base"]) / "state")
    folder = Path(private.name)
    number = str(100 + secrets.randbelow(30000))
    auth = folder / "nested.Xauthority"
    auth.touch(mode=0o600)
    cookie = secrets.token_hex(16)
    subprocess.run(["/usr/bin/xauth", "-f", str(auth)], input=f"add :{number} MIT-MAGIC-COOKIE-1 {cookie}\n", text=True,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    args = ["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc",
            "--new-session", "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--bind", str(folder), "/tmp"]
    for target in ("/usr", "/lib", "/lib64", "/etc/fonts", "/etc/alternatives", "/etc/ld.so.cache", "/etc/passwd", "/etc/group"):
        if Path(target).exists():
            args += ["--ro-bind", str(Path(target).resolve()), target]
    args += ["--symlink", "usr/bin", "/bin", "--ro-bind", parent_socket, parent_socket,
             "--ro-bind", str(authority.resolve()), "/parent-Xauthority", "--chdir", "/tmp", "--", "/usr/bin/Xephyr", ":" + number,
             "-screen", "1200x800", "-title", "Claude 专用浏览器", "-nolisten", "tcp", "-no-host-grab", "-noreset",
             "-extension", "MIT-SHM", "-extension", "GLX", "-extension", "DRI2", "-extension", "DRI3", "-auth", "/tmp/nested.Xauthority"]
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "DISPLAY": "unix/" + match[1], "XAUTHORITY": "/parent-Xauthority", "LANG": "en_US.UTF-8",
           "LIBGL_ALWAYS_SOFTWARE": "1", "__EGL_VENDOR_LIBRARY_FILENAMES": "/usr/share/glvnd/egl_vendor.d/50_mesa.json"}
    with (Path(m["base"]) / "state/display-startup.stderr").open("w") as errors:
        process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
    socket_path = folder / ".X11-unix" / ("X" + number)
    try:
        deadline = time.monotonic() + 10
        while not socket_path.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("专用浏览器显示服务启动失败；没有开放主机显示。")
            time.sleep(.1)
        return process, private, {"display_socket": str(socket_path), "display_number": number, "display_authority": str(auth)}
    except BaseException:
        process.terminate()
        process.wait(timeout=5)
        private.cleanup()
        raise


def prepare_profile(m):
    root = Path(m["profile_root"])
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            command = (proc / "cmdline").read_bytes()
            if not (("--user-data-dir=" + str(root)).encode() in command or str(root).encode() in command.split(b"\0")):
                continue
            if (proc / "exe").resolve() == Path("/opt/google/chrome/chrome"):
                raise RuntimeError("请先关闭 Claude 专用浏览器窗口；日常浏览器不用关闭。")
        except PermissionError:
            raise RuntimeError("无法核验浏览器进程归属，保留锁并拒绝启动。") from None
        except (FileNotFoundError, ProcessLookupError):
            continue
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = root / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            raise RuntimeError("专用浏览器锁格式异常，拒绝覆盖。")


def browser_proxy(parsed):
    """Add SDK proxy credentials for Chromium, which ignores URL userinfo."""
    credential = urllib.parse.unquote(parsed.username or "") + ":" + urllib.parse.unquote(parsed.password or "")
    authorization = b"Proxy-Authorization: Basic " + base64.b64encode(credential.encode()) + b"\r\n"

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(15)
                header = b""
                while b"\r\n\r\n" not in header:
                    chunk = self.request.recv(4096)
                    if not chunk or len(header) + len(chunk) > 65536:
                        return
                    header += chunk
                head, body = header.split(b"\r\n\r\n", 1)
                lines = [line for line in head.split(b"\r\n") if not line.lower().startswith(b"proxy-authorization:")]
                with socket.create_connection(("127.0.0.1", parsed.port), timeout=15) as upstream:
                    upstream.sendall(b"\r\n".join(lines) + b"\r\n" + authorization + b"\r\n" + body)
                    while True:
                        ready, _, _ = select.select([self.request, upstream], [], [], 60)
                        if not ready:
                            return
                        for source in ready:
                            chunk = source.recv(65536)
                            if not chunk:
                                return
                            (upstream if source is self.request else self.request).sendall(chunk)
            except OSError:
                return

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def open_links(m, urls):
    require_isolation(m)
    if os.environ.get("CLAUDE_BROWSER_ONLY_ACTIVE") != "1":
        raise RuntimeError("Browser link handler must run inside the browser-only session")
    for url in urls:
        local_json(m, "/json/new?" + urllib.parse.quote(validate_link(url), safe=""), "PUT")


def install(profile_id):
    home = Path.home()
    if not re.fullmatch(r"cp-[0-9a-f]{12}", profile_id):
        raise ValueError("Invalid managed browser profile ID")
    registry = json.loads((home / ".local/share/chrome-profile-grouping/registry.json").read_text())
    entry = registry["profiles"][profile_id]
    profile = home / ".config/chrome-profiles" / profile_id
    directory = entry["directory"]
    if profile.is_symlink() or not profile.is_dir() or Path(directory).name != directory:
        raise ValueError("Unsafe or missing dedicated profile")
    base = base_path()
    cli, node = resolve_installation(base)
    m = {"base": str(base), "host_home": str(home), "profile_root": str(profile),
         "profile_directory": directory, "node": str(node), "node_prefix": str(node.parent.parent),
         "cli": str(cli), "port": 19291,
         "host_namespaces": {name: os.readlink("/proc/self/ns/" + name) for name in ("user", "mnt", "pid", "net")}}
    for package, version in (("@anthropic-ai/sandbox-runtime", SRT_VERSION), ("chrome-devtools-mcp", MCP_VERSION)):
        p = base / "runtime/node_modules" / package / "package.json"
        if json.loads(p.read_text())["version"] != version:
            raise RuntimeError("Pinned sandbox/MCP dependency is missing")
    if not shutil.which("bwrap") or not shutil.which("socat"):
        raise RuntimeError("bubblewrap and socat are required; no unsandboxed fallback")
    for name in ("code/bin", "config", "state/home", "state/tmp", "state/workspace"):
        (base / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("browser-only.py", "browser-only-runtime.mjs", "claude-session.py"):
        source = Path(__file__).resolve().parent / name
        destination = base / "code" / name
        if source != destination:
            shutil.copyfile(source, destination)
        destination.chmod(0o700)
    for name, data in (("manifest.json", m), ("policy.json", make_policy(m)), ("mcp.json", mcp_config(m)), ("claude-command.json", claude_command(m)),
                       ("tool-container.json", tool_container(m)), ("auth-keys.json", sorted(AUTH_KEYS)),
                       ("settings.json", {"permissions": {"allow": ["mcp__claude-browser__*"]}})):
        path = base / "config" / name
        path.write_text(json.dumps(data, indent=2) + "\n")
        path.chmod(0o600)
    for name in ("browser-open", "xdg-open"):
        path = base / "code/bin" / name
        path.write_text('#!/bin/sh\nexec /usr/bin/python3 "' + str(base / "code/browser-only.py") + '" open "$@"\n')
        path.chmod(0o700)
    nested = base / "code/bin/claude"
    nested.write_text('#!/bin/sh\nexec /usr/bin/python3 "' + str(base / "code/browser-only.py") + '" nested -- "$@"\n')
    nested.chmod(0o700)
    launcher = home / ".local/bin/claude-browser-only"
    launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 "' + str(base / "code/browser-only.py") + '" run "$@"\n')
    launcher.chmod(0o700)
    desktop = home / ".local/share/applications/claude-browser-only.desktop"
    restriction = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
    command = desktop_session_command(base, launcher, restriction.exists() and restriction.read_text().strip() == "1")
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Claude 浏览器专用助手\nComment=完整开发工具与专用浏览器隔离\nTerminal=false\nExec=/usr/bin/gnome-terminal --wait --title="Claude 开发会话" -- ' + command + '\nIcon=' + str(Path(__file__).resolve().parents[1] / "assets/icon-2048.png") + '\nCategories=Utility;\n')
    desktop.chmod(0o755)
    desk = subprocess.check_output(["xdg-user-dir", "DESKTOP"], text=True).strip()
    if not Path(desk).is_absolute() or Path(desk) == home:
        raise RuntimeError("No independent desktop directory")
    shutil.copyfile(desktop, Path(desk) / "Claude浏览器专用助手.desktop")
    (Path(desk) / "Claude浏览器专用助手.desktop").chmod(0o755)
    subprocess.run(["gio", "set", str(Path(desk) / "Claude浏览器专用助手.desktop"), "metadata::trusted", "true"], check=False)
    print("Installed browser-only entry; the original claude command and MCP configuration were not modified.")


def inside(m, mode):
    require_isolation(m)
    extra = cli_arguments(json.loads(os.environ.pop("CLAUDE_BROWSER_ONLY_ARGUMENTS", "[]")))
    if mode == "inside-client":
        return subprocess.call(client_command(m) + extra, env={**os.environ, "SRT_ALLOW_NESTED_USERNS": "1"})
    ready_socket = os.environ.pop("CLAUDE_SERVICE_READY_SOCKET", None)
    ready_nonce = os.environ.pop("CLAUDE_SERVICE_NONCE", None)
    state = Path(m["base"]) / "state"
    # Chromium's singleton Unix socket must fit Linux's 108-byte path limit.
    # This /tmp is a private mount supplied by the outer sandbox.
    os.environ["TMPDIR"] = "/tmp"
    profile = m["profile_root"] if mode == "inside-service" else str(state / "probe-profile")
    configure_us_preferences(profile, m["profile_directory"])
    proxy = os.environ.get("HTTP_PROXY", "")
    parsed = urllib.parse.urlsplit(proxy)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.port:
        raise RuntimeError("Required sandbox network proxy missing")
    relay = browser_proxy(parsed)
    # Chromium is contained by the mandatory outer mount/PID/network sandbox.
    # Its own namespace sandbox cannot nest under this policy; never apply this
    # flag to any host/daily browser launch.
    display = os.environ.get("DISPLAY")
    if mode == "inside-service" and not display:
        raise RuntimeError("Private browser display missing")
    argv = ["/usr/bin/google-chrome", *([] if display else ["--headless=new"]), "--no-sandbox", "--disable-dev-shm-usage",
            "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-extensions",
            "--password-store=basic", "--lang=en-US", "--accept-lang=en-US,en", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--user-data-dir=" + profile, "--profile-directory=" + m["profile_directory"],
            "--proxy-server=http://127.0.0.1:" + str(relay.server_address[1]), "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=" + str(m["port"]), "about:blank"]
    diagnostic = (state / "workspace/probe-chrome.stderr").open("w") if mode == "inside-verify" else subprocess.DEVNULL
    chrome = subprocess.Popen(tool_container(m, display) + argv, env=tool_environment(os.environ), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=diagnostic)
    try:
        deadline = time.monotonic() + 20
        while True:
            if chrome.poll() is not None:
                raise RuntimeError("Isolated browser failed to start")
            try:
                version = local_json(m, "/json/version")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise RuntimeError("Isolated browser startup timed out") from None
                time.sleep(0.15)
        os.environ["CLAUDE_BROWSER_ONLY_WS"] = version["webSocketDebuggerUrl"]
        os.environ["CLAUDE_BROWSER_ONLY_ACTIVE"] = "1"
        if mode == "inside-service":
            open_links(m, ["https://claude.ai/"])
            if not ready_socket or not ready_nonce:
                raise RuntimeError("Browser service readiness channel missing")
            proxy_env = {key: os.environ[key] for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy") if key in os.environ}
            with socket.socket(socket.AF_UNIX) as channel:
                channel.connect(ready_socket)
                channel.sendall(json.dumps({"nonce": ready_nonce, "ws": version["webSocketDebuggerUrl"], "proxy_env": proxy_env}).encode())
            while chrome.poll() is None:
                time.sleep(.3)
            return 0
        return subprocess.call([m["node"], str(Path(m["base"]) / "code/browser-only-runtime.mjs"), "verify"])
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait()
        if diagnostic != subprocess.DEVNULL:
            diagnostic.close()
        relay.shutdown()
        relay.server_close()


def run_sandbox(m, verify=False, visible=False, service=False):
    base = Path(m["base"])
    lock = (base / "state/session.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("专用浏览器服务或隔离验证正在运行，请稍后重试。") from None
    if not verify:
        prepare_profile(m)
    config = Path(m["host_home"]) / ".claude-switcher/claude-switch.sh"
    target = re.search(r'^COUNTRY_TARGET="([A-Z]{2})"', config.read_text(), re.MULTILINE)
    with urllib.request.urlopen("https://www.cloudflare.com/cdn-cgi/trace", timeout=12) as response:
        trace = dict(line.split("=", 1) for line in response.read().decode().splitlines() if "=" in line)
    if not target or target[1] != "US" or trace.get("loc") != "US":
        raise RuntimeError("专用浏览器配置为美国；目标和出口国家均须为 US，拒绝不一致的启动。")
    auth = {"ANTHROPIC_AUTH_TOKEN": "harmless-cli-auth-canary-7fa91"} if verify else {}
    env = clean_environment(m, auth)
    env["CLAUDE_WORKSPACES"] = json.dumps(m.get("workspaces", []))
    mode = "inside-verify" if verify else "inside-service"
    display_process = display_folder = None
    if not verify or visible:
        display_process, display_folder, display_settings = start_display(m)
        m = {**m, **display_settings}
        env.update({"DISPLAY": ":" + m["display_number"], "XAUTHORITY": m["display_authority"]})
    if verify:
        # A benign host file and local server prove denial without reading any
        # cookie, password, history or real private file contents.
        marker = tempfile.NamedTemporaryFile(prefix="browser-only-canary-", dir=Path(m["host_home"]) / ".cache", delete=False)
        marker.write(b"harmless-host-canary"); marker.close()
        server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
        other = next(p for p in (Path(m["host_home"]) / ".config/chrome-profiles").glob("cp-*") if str(p) != m["profile_root"] and p.is_dir())
        uds_path = marker.name + ".sock"
        Path(uds_path).unlink(missing_ok=True)
        uds = socket.socket(socket.AF_UNIX); uds.bind(uds_path); uds.listen(1)
        canary = {"file": marker.name, "host_pid": os.getpid(), "host_port": server.getsockname()[1],
                  "other_profile": str(other), "unix_socket": uds_path}
        original_display = re.fullmatch(r"(?:unix/)?:(\d+)(?:\.\d+)?", os.environ.get("DISPLAY", ""))
        canary["host_display_socket"] = "/tmp/.X11-unix/X" + original_display[1] if original_display else None
        canary["host_display_authority"] = os.environ.get("XAUTHORITY")
        secret_canary = base / "state/home/cli-secret-canary"
        secret_canary.write_text(auth["ANTHROPIC_AUTH_TOKEN"])
        (base / "config/canary.json").write_text(json.dumps(canary))
        (base / "config/canary.json").chmod(0o600)
    control = None
    child = None
    if service:
        endpoint = base / "state" / ("browser-ready-" + secrets.token_hex(8) + ".sock")
        nonce = secrets.token_hex(24)
        control = socket.socket(socket.AF_UNIX)
        control.bind(str(endpoint)); endpoint.chmod(0o600)
        control.listen(1); control.settimeout(45)
        env.update({"CLAUDE_SERVICE_READY_SOCKET": str(endpoint), "CLAUDE_SERVICE_NONCE": nonce})
    try:
        if service:
            child = subprocess.Popen(outer_command(m, env, mode), env=env, cwd=base / "state/workspace")
            with control.accept()[0] as connection:
                pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                connection.settimeout(5)
                chunks = bytearray()
                while len(chunks) <= 65536:
                    part = connection.recv(4096)
                    if not part:
                        break
                    chunks.extend(part)
                data = json.loads(chunks)
            if uid != os.getuid() or data.pop("nonce", None) != nonce:
                raise RuntimeError("Browser service readiness rejected")
            status = Path(f"/proc/{pid}/stat").read_text()
            data.update(pid=pid, start=status[status.rfind(")") + 2:].split()[19],
                        namespaces={name: os.readlink(f"/proc/{pid}/ns/{name}") for name in ("user", "mnt", "net", "pid")})
            if any(data["namespaces"][name] == host for name, host in m["host_namespaces"].items()):
                raise RuntimeError("Browser service namespaces were not isolated")
            ready = base / "config/service.json"
            temporary = ready.with_suffix(".tmp")
            temporary.write_text(json.dumps(data)); temporary.chmod(0o600); os.replace(temporary, ready)
            control.close(); control = None; endpoint.unlink(missing_ok=True)
            return child.wait()
        return subprocess.call(outer_command(m, env, mode), env=env, cwd=base / "state/workspace")
    finally:
        if service:
            if control is not None:
                control.close()
            endpoint.unlink(missing_ok=True)
            (base / "config/service.json").unlink(missing_ok=True)
            if child is not None and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait()
        if verify:
            unchanged = Path(marker.name).read_bytes() == b"harmless-host-canary"
            Path(marker.name).unlink(missing_ok=True)
            server.close()
            uds.close()
            Path(uds_path).unlink(missing_ok=True)
            secret_canary.unlink(missing_ok=True)
            if not unchanged:
                raise RuntimeError("Host canary changed: sandbox boundary verification failed")
        if display_process is not None:
            display_process.terminate()
            try:
                display_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                display_process.kill()
                display_process.wait()
            display_folder.cleanup()
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("install", "run", "serve", "default", "nested", "verify", "verify-visible", "inside-client", "inside-service", "inside-verify", "inside-help", "inside-version", "inside-status", "open"))
    parser.add_argument("values", nargs="*")
    args = parser.parse_args()
    if args.mode == "install":
        install(args.values[0])
        return 0
    m = json.loads((base_path() / "config/manifest.json").read_text())
    if args.mode == "default":
        request = default_request(args.values)
        if request == "run":
            return subprocess.call(["/usr/bin/python3", str(base_path() / "code/claude-session.py"), *cli_arguments(args.values)])
        env = clean_environment(m, {})
        return subprocess.call(outer_command(m, env, request), env=env, cwd=Path(m["base"]) / "state/workspace")
    if args.mode == "nested":
        require_isolation(m)
        return subprocess.call(claude_command(m) + cli_arguments(args.values))
    if args.mode in ("inside-help", "inside-version", "inside-status"):
        require_isolation(m)
        extra = ["--help"] if args.mode == "inside-help" else ["--version"] if args.mode == "inside-version" else ["auth", "status", "--json"]
        return subprocess.call(claude_command(m) + extra)
    if args.mode == "open":
        open_links(m, args.values)
        return 0
    if args.mode in ("inside-client", "inside-service", "inside-verify"):
        return inside(m, args.mode)
    if args.values:
        parser.error("This entry accepts no CLI/tool override arguments")
    if args.mode == "run":
        return subprocess.call(["/usr/bin/python3", str(base_path() / "code/claude-session.py")])
    return run_sandbox(m, verify=args.mode in ("verify", "verify-visible"), visible=args.mode == "verify-visible", service=args.mode == "serve")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Do not disclose URLs, proxy addresses, tokens or browser contents.
        print(str(error) if isinstance(error, RuntimeError) else "Browser-only startup failed (" + type(error).__name__ + "); no unprotected fallback was started.", file=sys.stderr)
        raise SystemExit(1)
