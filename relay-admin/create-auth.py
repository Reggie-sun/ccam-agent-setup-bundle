#!/usr/bin/env python3
"""Create a new private admin-auth file without putting a password in argv."""
import getpass
import hashlib
import json
import os
import secrets
import sys

if len(sys.argv) != 2:
    raise SystemExit('Usage: python3 create-auth.py /etc/relay-admin/admin-auth')
password = getpass.getpass('New administrator password (at least 16 characters): ')
if len(password) < 16 or password != getpass.getpass('Repeat password: '):
    raise SystemExit('Passwords must match and have at least 16 characters')
salt = secrets.token_bytes(32)
record = {'username': 'admin', 'salt': salt.hex(), 'hash': hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()}
with os.fdopen(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as target:
    json.dump(record, target)
print('Created private authentication file; password was not printed.')
