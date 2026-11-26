#!/usr/bin/env python3
"""Select the single imported lab AWG exit and apply it without exposing keys."""
import json
from pathlib import Path
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8787/api'


def request(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=150) as response:
        result = response.read()
        return json.loads(result) if result else None


def main():
    password = Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
    token = request('POST', '/auth/login', {'username': 'admin', 'password': password})['access_token']
    state = request('GET', '/core', token=token)
    exits = state['saved_awg_egresses']
    if len(exits) != 1 or not state['agent'] or state['agent']['pending_generation_id']:
        raise ValueError('expected exactly one exit and no pending transaction')
    request('POST', '/core/awg-egresses/' + exits[0]['id'] + '/select', {'revision': state['revision']}, token)
    state = request('GET', '/core', token=token)
    request('PUT', '/core/rules', {'revision': state['revision'], 'rules': state['rules'],
                                   'default_egress': 'vpn'}, token)
    preview = request('POST', '/core/preview', token=token)
    print('Reviewed sections: ' + ', '.join(preview['sections']), flush=True)
    result = request('POST', '/core/apply', {'revision': preview['revision'],
                                             'base_generation': preview['base_generation']}, token)
    print('Apply status:', result['state'], 'active generation:', result['active_generation'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as exc:
        print('Lab API rejected operation, HTTP', exc.code, flush=True)
        raise SystemExit(1) from None
    except Exception:
        print('Lab API operation failed; inspect Agent status before retry', flush=True)
        raise SystemExit(1) from None
