#!/usr/bin/env python3
"""Emit the imported AWG runtime payload to a protected diagnostic file only."""
import base64
import hashlib
import hmac
import json
import sqlite3
import sys

from cryptography.fernet import Fernet
from app.core.awg_bridge import build_awg_bridge_config

try:
    secret = open('/run/core/app-secret').read().strip().encode()
    key = hmac.digest(secret, b'TEMPEST Working Core VLESS at rest v1', hashlib.sha256)
    with sqlite3.connect('file:/app/data/working-core.db?mode=ro', uri=True) as db:
        data = json.loads(db.execute('SELECT value FROM settings WHERE key = ?', ('tempest_working_core_v1',)).fetchone()[0])
    exits = data['saved_awg_egresses']
    if len(exits) != 1:
        raise ValueError
    raw = Fernet(base64.urlsafe_b64encode(key)).decrypt(exits[0]['encrypted_config'].encode()).decode()
    sys.stdout.buffer.write(build_awg_bridge_config(raw, socks_port=18877, socks_password='a' * 64).payload)
except Exception:
    print('Diagnostic config could not be prepared', file=sys.stderr)
    raise SystemExit(1) from None
