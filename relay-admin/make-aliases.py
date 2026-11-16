#!/usr/bin/env python3
"""Write aliases.json (email -> local ccam profile name) from this machine's ccam profiles.

Upload the result next to server.py on the VPS; the admin page shows the alias beside each email.
Contains account emails only, no credentials. Not committed (see .gitignore).
Re-run and re-upload after a local ccam profile logs into a different account.
"""
import json
from pathlib import Path

home = Path.home()
sources = {'main': home / '.claude.json'}
profiles = home / '.local/share/ccam/profiles'
if profiles.is_dir():
    sources.update({d.name: d / '.claude.json' for d in sorted(profiles.iterdir()) if d.is_dir()})
aliases = {}
for alias, path in sources.items():
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    account = data.get('oauthAccount') if isinstance(data, dict) else None
    email = account.get('emailAddress') if isinstance(account, dict) else None
    if isinstance(email, str) and email:
        if email.lower() in aliases:
            print(f'note: {alias} replaces {aliases[email.lower()]} (same account)')
        aliases[email.lower()] = alias
out = Path(__file__).with_name('aliases.json')
out.write_text(json.dumps(aliases, ensure_ascii=False, indent=1, sort_keys=True))
out.chmod(0o644)
print(f'{len(aliases)} aliases -> {out}')
