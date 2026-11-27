#!/usr/bin/env python3
"""Create a protected draft without the old wg-clients endpoint; never edit input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

TARGET = 'wg-clients'


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def refs(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from refs(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from refs(child, path + (index,))
    elif value == TARGET:
        yield path


def build(content, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected) or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('source SHA-256 mismatch')
    data = json.loads(content, object_pairs_hook=unique_pairs)
    if not isinstance(data, dict) or not isinstance(data.get('route'), dict):
        raise ValueError('expected configuration and route objects')
    endpoints = data.get('endpoints')
    routes = data.get('route', {}).get('rules')
    if not isinstance(endpoints, list) or not isinstance(routes, list):
        raise ValueError('missing endpoint or route list')
    matches = [i for i, ep in enumerate(endpoints) if isinstance(ep, dict) and ep.get('tag') == TARGET]
    if len(matches) != 1:
        raise ValueError('expected exactly one wg-clients endpoint')
    idx = matches[0]
    if endpoints[idx].get('type') != 'wireguard' or endpoints[idx].get('listen_port') != 51820:
        raise ValueError('unexpected old endpoint selector')
    rules = [(i, r) for i, r in enumerate(routes) if isinstance(r, dict) and isinstance(r.get('inbound'), list) and TARGET in r['inbound']]
    if len(rules) != 1 or rules[0][1]['inbound'].count(TARGET) != 1 or rules[0][1].get('invert', False) is not False:
        raise ValueError('unexpected old inbound rule')
    rule_idx, rule = rules[0]
    expected_refs = {('endpoints', idx, 'tag'), ('route', 'rules', rule_idx, 'inbound', rule['inbound'].index(TARGET))}
    if set(refs(data)) != expected_refs:
        raise ValueError('unexpected wg-clients reference')
    del endpoints[idx]
    rule['inbound'].remove(TARGET)
    if not rule['inbound']:
        del routes[rule_idx]
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source_fd = os.open(args.source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError('source is not a regular file')
        with os.fdopen(source_fd, 'rb', closefd=False) as f:
            content = f.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError('source exceeds limit')
        result = build(content, args.source_sha256)
    finally:
        os.close(source_fd)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(result)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    print('Protected draft created; no service, input or firewall was changed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f'NOT CREATED: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
