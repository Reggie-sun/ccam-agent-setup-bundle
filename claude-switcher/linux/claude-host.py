#!/usr/bin/python3
"""Normal Claude tools, existing Pro login, and on-demand browser selection."""
from pathlib import Path
import json
import os
import sys


def arguments(values):
    end = values.index("--") if "--" in values else len(values)
    return [value for value in values[:end] if value not in ("--yolo", "--dangerously-skip-permissions")] + values[end:]


def passthrough(values):
    if values in (["--version"], ["-v"], ["--help"], ["-h"]):
        return True
    head = next((value for value in values if value != "--" and not value.startswith("-")), None)
    if head in {"auth", "mcp", "plugin", "plugins", "update", "doctor", "config", "setup-token", "install", "uninstall", "migrate-installer", "serve"}:
        return True
    return "-p" in values or "--print" in values


def command(manifest, values):
    values = arguments(values)
    base = Path(manifest["base"])
    program = [manifest["node"], manifest["cli"]] if Path(manifest["cli"]).suffix == ".js" else [manifest["cli"]]
    if passthrough(values):
        return program + values
    return program + ["--dangerously-skip-permissions", "--mcp-config", str(base / "config/multi-browser-mcp.json"),
                      "--append-system-prompt", "Browser automation can use any registered Chrome. Use browser_profiles then browser_select to choose the user's intended browser. Never assume a browser is tied to the Claude account. Normal development tools are available.", *values]


def environment(manifest):
    incoming_config = os.environ.get("CLAUDE_CONFIG_DIR")
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("ANTHROPIC_", "CLAUDE_BROWSER_ONLY_")) or key in ("CLAUDE_WORKSPACES", "CLAUDE_SERVICE_NONCE", "CLAUDE_SERVICE_READY_SOCKET"):
            env.pop(key)
    base = Path(manifest["base"])
    env.update({"HOME": manifest["host_home"], "CLAUDE_CONFIG_DIR": incoming_config or str(base / "state/home/.claude"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"})
    env["PATH"] = ":".join(part for part in env.get("PATH", "/usr/bin:/bin").split(":") if part != str(base / "code/bin"))
    if str(base / "code") in env.get("BROWSER", ""):
        env.pop("BROWSER")
    return env


def main():
    base = Path(__file__).resolve().parent.parent
    manifest = json.loads((base / "config/manifest.json").read_text())
    argv = command(manifest, sys.argv[1:])
    os.execve(argv[0], argv, environment(manifest))


if __name__ == "__main__":
    main()
