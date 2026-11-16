#!/usr/bin/env python3
"""Apply the reviewed v6 cu daemon patch (usage_observed_at + failed-request log) to an exact cu 1.4.0 daemon.py.

Only two exact daemon.py builds are accepted: the original cu 1.4.0 file and the same file after
install_auth_monitor.py. Anything else is refused unchanged. Never restarts services.
Rollback: --rollback BACKUP_DIR restores the saved daemon.py if the patched file is still untouched.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import tempfile

# exact source daemon.py sha256 -> sha256 after cu_daemon_v6.patch
SOURCES = {
    'c53f3090b6548c3b841f2535b1cde2bb8e0cc1f4b4c5c3b485e7fc5fb6a67da0': '09f1de61fe8b054a5c22804c13682494ea259091f899e2de4c55693f477b71cf',  # cu 1.4.0
    '732993aa2436cfc8823be431399f249969c35b07acb8b47055d925b0d9d7b66d': 'db9193ed70c96831d065da5fe4796d16c11c7c798a31022f029902b45c54b0d2',  # cu 1.4.0 + v5 auth monitor
}
PATCH_FILE = Path(__file__).with_name('cu_daemon_v6.patch')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def apply_hunks(text, diff):
    """Replace each unified-diff hunk's old lines with its new lines; each old block must occur exactly once."""
    for hunk in diff.split('\n@@ ')[1:]:
        lines = hunk.splitlines(keepends=True)[1:]
        old = ''.join(line[1:] for line in lines if line[0] in ' -')
        new = ''.join(line[1:] for line in lines if line[0] in ' +')
        if text.count(old) != 1:
            raise ValueError('Patch context not found exactly once; inspect this build instead of forcing a patch')
        text = text.replace(old, new)
    return text


def patch_daemon(data, diff):
    digest = sha256(data)
    if digest in SOURCES.values():
        return data
    if digest not in SOURCES:
        raise ValueError('Unsupported or modified cu daemon; inspect this build instead of forcing a patch')
    patched = apply_hunks(data.decode(), diff).encode()
    if sha256(patched) != SOURCES[digest]:
        raise ValueError('Patched daemon does not match the reviewed result; nothing was changed')
    compile(patched, 'daemon.py', 'exec')
    return patched


def replace_atomically(path, data):
    """Write a temp file in the same directory, fsync, copy mode and owner, then os.replace: never a partial daemon.py."""
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, temp)
        status = path.stat()
        os.chown(temp, status.st_uid, status.st_gid)  # refuses (nothing replaced) if this user cannot keep the owner
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise


def install(package, patch_file=PATCH_FILE):
    daemon = package / 'daemon.py'
    before = daemon.read_bytes()
    after = patch_daemon(before, patch_file.read_text())
    if before == after:
        return None
    backup = package / ('cu-patch-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(daemon, backup / 'daemon.py')
    (backup / 'manifest.json').write_text(json.dumps({'before': sha256(before), 'after': sha256(after)}))
    replace_atomically(daemon, after)
    return backup


def rollback(package, backup):
    daemon = package / 'daemon.py'
    if not (backup / 'daemon.py').is_file():
        raise ValueError(f'No daemon.py in {backup}; pass a cu-patch-backup-* directory printed by an earlier install')
    saved = (backup / 'daemon.py').read_bytes()
    current = daemon.read_bytes()
    if SOURCES.get(sha256(saved)) != sha256(current):
        raise ValueError('daemon.py is not the patched form of this backup; restore manually after inspection')
    replace_atomically(daemon, saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rollback', type=Path, metavar='BACKUP_DIR', help='restore daemon.py from a backup printed by an earlier install')
    args = parser.parse_args()
    try:
        installed = version('claude-unlimited')
    except PackageNotFoundError:
        installed = None
    if installed is None:
        raise SystemExit('cu (claude-unlimited) not found; run with the Python interpreter from the existing cu environment')
    if installed != '1.4.0':
        raise SystemExit('Only the reviewed cu 1.4.0 build is supported')
    spec = importlib.util.find_spec('claude_unlimited')
    if spec is None or not spec.origin:
        raise SystemExit('Run with the Python interpreter from the existing cu environment')
    package = Path(spec.origin).parent
    try:
        if args.rollback:
            rollback(package, args.rollback)
            print(f'Restored daemon.py from {args.rollback}')
        else:
            backup = install(package)
            print('Already installed' if backup is None else f'Installed; backup: {backup}')
    except ValueError as error:
        raise SystemExit(str(error))
    except OSError as error:  # e.g. not run as the owner of the cu install; nothing was replaced
        raise SystemExit(f'Could not write {error.filename or "daemon.py"}: {error.strerror}; daemon.py unchanged')
    print('No service restarted. Verify no active requests/login, then restart your cu service.')


if __name__ == '__main__':
    main()
