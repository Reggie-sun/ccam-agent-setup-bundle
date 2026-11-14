#!/usr/bin/env python3
"""Install the reviewed monitor into an exact cu 1.4.0 build; never restart services."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
from importlib.metadata import version
import json
from pathlib import Path
import shutil

BASE_SHA256 = 'c53f3090b6548c3b841f2535b1cde2bb8e0cc1f4b4c5c3b485e7fc5fb6a67da0'
OLD = '            _gateway.runtime_snapshot()\n        except Exception:\n            pass\n\n\ndef run_foreground'
NEW = '            _gateway.runtime_snapshot()\n            from . import auth_monitor\n            auth_monitor.tick(_gateway)\n        except Exception:\n            pass\n\n\ndef run_foreground'


def patch_daemon(data):
    text = data.decode()
    if NEW in text and '_OAUTH_REFRESH_LOOP_INTERVAL_SECONDS = 30' in text:
        original = text.replace(NEW, OLD).replace('_OAUTH_REFRESH_LOOP_INTERVAL_SECONDS = 30', '_OAUTH_REFRESH_LOOP_INTERVAL_SECONDS = 60')
        if hashlib.sha256(original.encode()).hexdigest() == BASE_SHA256:
            return data
    if hashlib.sha256(data).hexdigest() != BASE_SHA256 or text.count(OLD) != 1:
        raise ValueError('Unsupported or modified cu daemon; inspect this build instead of forcing a patch')
    text = text.replace(OLD, NEW).replace('_OAUTH_REFRESH_LOOP_INTERVAL_SECONDS = 60', '_OAUTH_REFRESH_LOOP_INTERVAL_SECONDS = 30')
    compile(text, 'daemon.py', 'exec')
    return text.encode()


def install(package, source):
    daemon = package / 'daemon.py'
    target = package / 'auth_monitor.py'
    before = daemon.read_bytes()
    after = patch_daemon(before)
    module = source.read_bytes()
    compile(module, str(source), 'exec')
    if before == after and target.exists() and target.read_bytes() == module:
        return None
    backup = package / ('auth-monitor-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(daemon, backup / 'daemon.py')
    if target.exists():
        shutil.copy2(target, backup / 'auth_monitor.py')
    (backup / 'manifest.json').write_text(json.dumps({'module_existed': target.exists()}))
    try:
        target.write_bytes(module)
        target.chmod(0o644)
        daemon.write_bytes(after)
    except Exception:
        shutil.copy2(backup / 'daemon.py', daemon)
        if (backup / 'auth_monitor.py').exists():
            shutil.copy2(backup / 'auth_monitor.py', target)
        elif target.exists():
            target.unlink()
        raise
    return backup


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if version('claude-unlimited') != '1.4.0':
        raise SystemExit('Only the reviewed cu 1.4.0 build is supported')
    spec = importlib.util.find_spec('claude_unlimited')
    if spec is None or not spec.origin:
        raise SystemExit('Run with the Python interpreter from the existing cu environment')
    backup = install(Path(spec.origin).parent, Path(__file__).with_name('cu_auth_monitor.py'))
    print('Already installed' if backup is None else f'Installed; backup: {backup}')
    print('No service restarted. Verify no active requests/login, then restart your cu service.')


if __name__ == '__main__':
    main()
