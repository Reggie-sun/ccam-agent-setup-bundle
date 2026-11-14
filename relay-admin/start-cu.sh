#!/bin/sh
set -eu
/usr/bin/gnome-keyring-daemon --unlock --components=secrets < "$CREDENTIALS_DIRECTORY/keyring-pass" >/dev/null
exec /opt/claude-relay/venv/bin/python -m claude_unlimited start --port 4317
