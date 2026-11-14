#!/usr/bin/env python3
"""Back up and merge private VPS settings; optionally install a Linux CLI entry."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

KEYS = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_CUSTOM_HEADERS')
START = '# BEGIN CCAM VPS DEFAULT'
END = '# END CCAM VPS DEFAULT'
LAUNCHER = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
config = json.loads((Path.home()/'.config/claude-relay/client.json').read_text())
env = {k:v for k,v in os.environ.items() if not k.startswith('ANTHROPIC_') and k != 'CLAUDE_CODE_OAUTH_TOKEN'}
env.update(config['env'])
program = config['claude_binary']
os.execve(program, [program, *sys.argv[1:]], env)
'''


class ConfigurationWriteError(ValueError):
    pass


def atomic_write(path, data, mode):
    fd, temporary = tempfile.mkstemp(prefix='.claude-vps-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(home, source, binary=None):
    raw = json.loads(source.read_text())
    values = {k: raw.get(k, '') for k in KEYS}
    if any(not isinstance(v, str) for v in values.values()):
        raise ValueError('Configuration values must be strings')
    url = urlsplit(values['ANTHROPIC_BASE_URL'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use your own HTTPS relay base URL without credentials, query or fragment')
    if not values['ANTHROPIC_AUTH_TOKEN'] or any(c in values['ANTHROPIC_AUTH_TOKEN'] for c in '\r\n'):
        raise ValueError('A valid relay API token is required')
    if binary:
        binary = binary.expanduser().resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError('Claude binary must be an executable file')
        if binary in [(home/'.local/bin/claude').resolve(), (home/'.local/bin/claude-vps').resolve()]:
            raise ValueError('Select the official Claude binary, not a relay wrapper')
    updates = {}
    settings = [home/'.claude/settings.json', *sorted(home.glob('.local/share/ccam/profiles/*/settings.json'))]
    for path in settings:
        data = json.loads(path.read_text()) if path.exists() else {}
        env = data.setdefault('env', {})
        if not isinstance(env, dict):
            raise ValueError('Existing env settings must be an object')
        env.update(values)
        for key in ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'):
            env.pop(key, None)
        updates[path] = (json.dumps(data, ensure_ascii=False, indent=2)+'\n', 0o600)
    if binary:
        updates[home/'.config/claude-relay/client.json'] = (json.dumps({'env': values, 'claude_binary': str(binary)}, indent=2)+'\n', 0o600)
        updates[home/'.local/bin/claude-vps'] = (LAUNCHER, 0o700)
        updates[home/'.local/bin/claude'] = ('#!/bin/sh\nexec "$HOME/.local/bin/claude-vps" "$@"\n', 0o700)
        bashrc = home/'.bashrc'
        text = bashrc.read_text() if bashrc.exists() else ''
        block = START+'\nunalias claude 2>/dev/null || :\nclaude() { command "$HOME/.local/bin/claude" "$@"; }\n'+END
        if START in text or END in text:
            if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
                raise ValueError('Malformed managed shell block; inspect before changing it')
            text = text[:text.index(START)] + block + text[text.index(END)+len(END):]
        else:
            text = text.rstrip()+'\n\n'+block+'\n'
        updates[bashrc] = (text, bashrc.stat().st_mode & 0o777 if bashrc.exists() else 0o600)
    # Refuse symlinks rather than accidentally changing a shared binary or config.
    if any(p.is_symlink() for p in updates):
        raise ValueError('A target is a symlink; inspect it and choose settings-only mode or a separate launcher')
    changed = {p: value for p, value in updates.items() if not p.exists() or p.read_text() != value[0] or p.stat().st_mode & 0o777 != value[1]}
    if not changed:
        return None
    backup = home/('.claude-vps-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700)
    manifest = []
    for path in changed:
        relative = path.relative_to(home)
        manifest.append({'path': str(relative), 'existed': path.exists()})
        if path.exists():
            target = backup/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    (backup/'manifest.json').write_text(json.dumps(manifest, indent=2))
    applied = []
    try:
        for path, (text, mode) in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, text.encode(), mode)
            applied.append(path)
    except OSError:
        failed_restore = False
        for path in reversed(applied):
            saved = backup/path.relative_to(home)
            try:
                if saved.exists():
                    atomic_write(path, saved.read_bytes(), saved.stat().st_mode & 0o777)
                else:
                    path.unlink()
            except OSError:
                failed_restore = True
        status = 'Rollback incomplete; inspect affected paths' if failed_restore else 'Updated files rolled back'
        raise ConfigurationWriteError(f'{status}. Private backup: {backup}') from None
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-file', type=Path, required=True, help='Private JSON containing your relay environment values')
    parser.add_argument('--claude-bin', type=Path, help='Linux: exact official executable; omit for settings only')
    args = parser.parse_args()
    try:
        backup = configure(Path.home(), args.config_file, args.claude_bin)
    except ConfigurationWriteError as exc:
        raise SystemExit(str(exc)) from None
    except (ValueError, OSError, TypeError, AttributeError):
        raise SystemExit('Configuration failed; inspect input/target files privately. No credential values are printed.')
    print('Already configured' if backup is None else f'Configured; private backup: {backup}')
    print('Running sessions were not killed. Open a new terminal and restart Claude to use the new route.')


if __name__ == '__main__':
    main()
