#!/usr/bin/python3
"""Register the ordinary Claude command without changing its native backend."""
from pathlib import Path
import datetime
import json
import os
import re
import shutil
import tempfile

MARKER = "# claude-switcher protected default"
BLOCK = '\n' + MARKER + '\nif [ -r "$HOME/.local/share/claude-browser-only/code/default-shell.sh" ]; then\n    . "$HOME/.local/share/claude-browser-only/code/default-shell.sh"\nfi\n'
SHELL_SOURCE = MARKER + '\nunalias claude 2>/dev/null || :\nclaude() {\n    "$HOME/.local/bin/claude" "$@"\n}\n'
LEGACY_BODIES = {
    ('command claude "$@"',),
    ('command claude --dangerously-skip-permissions "$@"',),
    ('if [[ "$1" == "--yolo" ]]; then', 'shift', 'command claude --dangerously-skip-permissions "$@"', 'else', 'command claude "$@"', 'fi'),
}


def shell_config(text):
    # Delete only recognized legacy wrappers. A shell function is not a regex
    # span ending at the next top-level brace; unknown bodies stay untouched.
    lines, kept, index = text.splitlines(keepends=True), [], 0
    while index < len(lines):
        header = lines[index].strip()
        if re.match(r"(?:function\s+)?claude(?:\s*\(|\s*\{)", header):
            if not re.fullmatch(r"(?:claude\s*\(\s*\)|function\s+claude(?:\s*\(\s*\))?)\s*\{", header):
                raise RuntimeError("Unknown claude function format; shell configuration preserved")
            end = index + 1
            while end < len(lines) and lines[end].strip() != "}":
                end += 1
            body = tuple(line.strip() for line in lines[index + 1:end] if line.strip())
            if end == len(lines) or body not in LEGACY_BODIES:
                raise RuntimeError("Unknown claude function body; shell configuration preserved")
            index = end + 1
            continue
        kept.append(lines[index])
        index += 1
    text = "".join(kept)
    # Existing aliases can be embedded in other user commands. Preserve those
    # lines and clear the effective alias in the shared source before defining
    # the one protected function.
    text = text.replace(BLOCK, "")
    return text.rstrip("\n") + "\n" + BLOCK


def wrapper_source(base):
    script = str(Path(base) / "code/claude-host.py")
    if any(c in script for c in '\n\r"`$\\'):
        raise RuntimeError("Unsupported launcher path")
    return '#!/bin/sh\n' + MARKER + '\nexec /usr/bin/python3 "' + script + '" "$@"\n'


def atomic_text(path, text, mode):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(text)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def install_default(home, base):
    home, base = Path(home), Path(base)
    manifest = json.loads((base / "config/manifest.json").read_text())
    backend, node = Path(manifest["cli"]), Path(manifest["node"])
    if not backend.is_file() or not node.is_file():
        raise RuntimeError("Original CLI backend is unavailable")
    launcher = home / ".local/bin/claude"
    npm_entry = node.parent / "claude"
    if npm_entry.resolve() not in (backend.resolve(), launcher.resolve()):
        raise RuntimeError("Unknown npm CLI entry; preserved without changes")
    desired = wrapper_source(base)
    if launcher.exists() and launcher.read_text() != desired and MARKER not in launcher.read_text():
        raise RuntimeError("An unrelated local claude entry exists; preserved without changes")
    configs = []
    for name in (".bashrc", ".zshrc", ".profile"):
        path = home / name
        if path.is_symlink():
            raise RuntimeError("Shell configuration symlink requires explicit ownership")
        old = path.read_text() if path.exists() else ""
        configs.append((path, old, shell_config(old), path.stat().st_mode & 0o777 if path.exists() else 0o600))
    backup_root = base / "backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="default-" + datetime.datetime.now().strftime("%Y%m%d-") , dir=backup_root))
    record = {"npm_entry": str(npm_entry), "original_target": os.readlink(npm_entry) if npm_entry.is_symlink() else None}
    (backup / "original.json").write_text(json.dumps(record, indent=2))
    (backup / "original.json").chmod(0o600)
    for path, old, new, mode in configs:
        if path.exists():
            saved = backup / path.name
            shutil.copyfile(path, saved)
            saved.chmod(0o600)
        if path.exists() and path.read_text() != old:
            raise RuntimeError("Shell configuration changed concurrently; preserved")
        if old != new:
            atomic_text(path, new, mode)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(base / "code/default-shell.sh", SHELL_SOURCE, 0o600)
    atomic_text(launcher, desired, 0o700)
    # Replace the npm symlink itself; never follow it and overwrite cli.js.
    temporary = npm_entry.with_name(".claude-default-" + os.urandom(6).hex())
    try:
        temporary.symlink_to(launcher)
        os.replace(temporary, npm_entry)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


if __name__ == "__main__":
    home = Path.home()
    backup = install_default(home, home / ".local/share/claude-browser-only")
    print("默认 claude 已启用正常工具及按需选择浏览器。备份：" + str(backup))
