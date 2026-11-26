#!/usr/bin/env python3
"""Reject a structurally valid but runtime-invalid AWG candidate in the lab."""
import base64
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import urllib.error

VM = 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609'
JOURNAL = Path('/var/lib/tempest-xray-agent/xray.json')


def main():
    if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != VM:
        raise SystemExit('Wrong VM or UID')
    spec = importlib.util.spec_from_loader('lab_api', importlib.machinery.SourceFileLoader('lab_api', '/home/gary/tempest-awg-stage/apply.py'))
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    password = Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
    token = api.request('POST', '/auth/login', {'username': 'admin', 'password': password})['access_token']
    state = api.request('GET', '/core', token=token)
    if len(state['saved_awg_egresses']) != 1 or state['agent']['pending_generation_id']:
        raise ValueError('unexpected lab state')
    good_id = state['saved_awg_egresses'][0]['id']
    generation = state['agent']['active_generation']
    before = set(json.loads(JOURNAL.read_text())['revoked'])
    key = base64.b64encode(b'\x11' * 32).decode()
    config = (f'[Interface]\nPrivateKey = {key}\nAddress = 10.0.0.2/32\nDNS = 1.1.1.1\nJc = 0\n'
              f'[Peer]\nPublicKey = {key}\nEndpoint = 192.0.2.123:1234\nAllowedIPs = 0.0.0.0/0\n')
    bad_id = None
    rejected = False
    try:
        bad = api.request('POST', '/core/awg-egresses', {'revision': state['revision'],
                          'name': 'Lab invalid AWG', 'config': config}, token)
        bad_id = bad['id']
        state = api.request('GET', '/core', token=token)
        api.request('POST', '/core/awg-egresses/' + bad_id + '/select', {'revision': state['revision']}, token)
        preview = api.request('POST', '/core/preview', token=token)
        try:
            api.request('POST', '/core/apply', {'revision': preview['revision'],
                        'base_generation': preview['base_generation']}, token)
        except urllib.error.HTTPError as error:
            rejected = error.code == 409
        state = api.request('GET', '/core', token=token)
        after = set(json.loads(JOURNAL.read_text())['revoked'])
        results = {'invalid_rejected': rejected,
                   'active_lkg_unchanged': state['agent']['active_generation'] == generation == state['agent']['last_known_good_generation'],
                   'revocations_preserved': before <= after}
        for name, ok in results.items():
            print(name, 'PASS' if ok else 'FAIL', flush=True)
        if not all(results.values()):
            raise SystemExit(1)
    finally:
        if bad_id:
            state = api.request('GET', '/core', token=token)
            if not state['agent'] or state['agent']['pending_generation_id']:
                raise RuntimeError('Pending transaction; leaving draft intact')
            if state['selected_egress_id'] == bad_id:
                api.request('POST', '/core/awg-egresses/' + good_id + '/select', {'revision': state['revision']}, token)
                state = api.request('GET', '/core', token=token)
            api.request('DELETE', '/core/awg-egresses/' + bad_id, {'revision': state['revision']}, token)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Lab invalid-candidate test did not complete; inspect draft and Agent', flush=True)
        raise SystemExit(1) from None
