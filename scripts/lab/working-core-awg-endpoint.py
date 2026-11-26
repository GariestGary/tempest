#!/usr/bin/env python3
"""Emit only the single imported AWG endpoint to a protected lab file."""
import base64
import configparser
import hashlib
import hmac
import ipaddress
import json
import sqlite3
import sys

from cryptography.fernet import Fernet


def main():
    with open('/run/core/app-secret') as file:
        secret = file.read().strip().encode()
    key = hmac.digest(secret, b'TEMPEST Working Core VLESS at rest v1', hashlib.sha256)
    with sqlite3.connect('file:/app/data/working-core.db?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT value FROM settings WHERE key = ?', ('tempest_working_core_v1',)).fetchone()
    exits = json.loads(row[0])['saved_awg_egresses']
    if len(exits) != 1:
        raise ValueError('expected exactly one imported AWG endpoint')
    raw = Fernet(base64.urlsafe_b64encode(key)).decrypt(exits[0]['encrypted_config'].encode()).decode()
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(raw.removeprefix('\ufeff'))
    host, port = parser['Peer']['Endpoint'].strip().rsplit(':', 1)
    ip = ipaddress.ip_address(host)
    if ip.version != 4 or not ip.is_global or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('lab relay supports only a global IPv4 endpoint')
    print(f'{ip}:{int(port)}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Could not prepare the single AWG endpoint for the lab relay', file=sys.stderr)
        raise SystemExit(1) from None
