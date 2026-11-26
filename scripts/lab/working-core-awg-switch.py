#!/usr/bin/env python3
"""Switch a saved lab VLESS exit and back to AWG without changing the WG peer."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess

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
    awg_id = state['saved_awg_egresses'][0]['id']
    identity = subprocess.check_output(['ip', 'netns', 'exec', 'tpab-w', 'wg', 'show', 'tpab-user', 'public-key'], text=True).strip()
    before = set(json.loads(JOURNAL.read_text())['revoked'])
    code = ("import json,sqlite3; db=sqlite3.connect('/app/data/working-core.db'); "
            "d=json.loads(db.execute('select value from settings where key=?',('tempest_working_core_v1',)).fetchone()[0]); "
            "m=[x['id'] for x in d['saved_egresses'] if x['encrypted_vless']==d['encrypted_vless']]; "
            "assert len(m)==1; print(m[0])")
    vless_id = subprocess.check_output(['docker', 'exec', 'tempest-working-core-api-1', 'python', '-c', code], text=True).strip()
    vless_pass = False
    awg_restored = False

    def select_apply(kind, identifier):
        current = api.request('GET', '/core', token=token)
        prefix = '/core/egresses/' if kind == 'vless' else '/core/awg-egresses/'
        api.request('POST', prefix + identifier + '/select', {'revision': current['revision']}, token)
        preview = api.request('POST', '/core/preview', token=token)
        return api.request('POST', '/core/apply', {'revision': preview['revision'],
                           'base_generation': preview['base_generation']}, token)

    try:
        select_apply('vless', vless_id)
        command = ['python3', '/home/gary/tempest/scripts/lab/working-core-traffic.py']
        warmup = subprocess.run([*command, '--warmup'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        check = subprocess.run([*command, '--positive-only'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=75)
        vless_pass = warmup.returncode == 0 and check.returncode == 0
    finally:
        recovered = select_apply('awg', awg_id)
        awg_restored = recovered['active_generation'] == recovered['last_known_good_generation'] > 0

    identity_after = subprocess.check_output(['ip', 'netns', 'exec', 'tpab-w', 'wg', 'show', 'tpab-user', 'public-key'], text=True).strip()
    after = json.loads(JOURNAL.read_text())
    results = {'vless_user_traffic': vless_pass, 'awg_restored': awg_restored,
               'client_profile_unchanged': identity == identity_after,
               'revocations_preserved': before <= set(after['revoked'])}
    for name, ok in results.items():
        print(name, 'PASS' if ok else 'FAIL', flush=True)
    if not all(results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Lab switching test incomplete; inspect active generation and draft', flush=True)
        raise SystemExit(1) from None
