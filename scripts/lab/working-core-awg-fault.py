#!/usr/bin/env python3
"""Fault an AWG process in the approved lab and recover without reverting revocations."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

UUID = 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609'


def user_checks():
    from xray_mvp_probe_checks import fixture_controls
    import importlib.machinery
    import importlib.util
    path = '/opt/tempest/bin/working-core-awg-traffic'
    spec = importlib.util.spec_from_loader('traffic', importlib.machinery.SourceFileLoader('traffic', path))
    traffic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(traffic)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {'dns_tcp_blocked': pool.submit(traffic.dns, '+tcp'),
                   'dns_udp_blocked': pool.submit(traffic.dns, '+notcp'),
                   'https_blocked': pool.submit(traffic.https),
                   'udp_blocked': pool.submit(traffic.udp_ntp),
                   'direct_and_block': pool.submit(fixture_controls)}
        results = {key: future.result() for key, future in futures.items()}
    for key in ('dns_tcp_blocked', 'dns_udp_blocked', 'https_blocked', 'udp_blocked'):
        results[key] = not results[key]
    print(json.dumps(results))


def revoked(path):
    with open(path) as file:
        return set(json.load(file)['revoked'])


def main():
    if sys.argv[1:] == ['user']:
        user_checks()
        return
    if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != UUID:
        raise SystemExit('Wrong VM or UID')
    sys.path.insert(0, '/home/gary/tempest-awg-stage')
    import importlib.machinery
    import importlib.util
    path = '/home/gary/tempest-awg-stage/apply.py'
    spec = importlib.util.spec_from_loader('lab_api', importlib.machinery.SourceFileLoader('lab_api', path))
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    password = Path('/var/lib/tempest-core-private/bootstrap-password').read_text().strip()
    token = api.request('POST', '/auth/login', {'username': 'admin', 'password': password})['access_token']
    original_revoked = revoked('/var/lib/tempest-xray-agent/xray.json')
    results = {}
    relay_fault = sys.argv[1:] == ['relay']
    if sys.argv[1:] not in ([], ['relay']):
        raise ValueError('unknown fault')
    try:
        unit = 'tempest-lab-awg-relay.service' if relay_fault else 'tempest-awgproxy.service'
        subprocess.run(['systemctl', 'stop', unit], check=True, timeout=10)
        user = subprocess.run(['ip', 'netns', 'exec', 'tpab-w', '/usr/bin/python3',
                               '/opt/tempest/bin/working-core-awg-fault', 'user'],
                              capture_output=True, text=True, timeout=90, check=True)
        results = json.loads(user.stdout)
        state = api.request('GET', '/core', token=token)['agent']
        if relay_fault:
            probed = api.request('POST', '/core/awg-egresses/probe', token=token)
            results['unavailable_reported'] = probed['egress_state'] == 'unavailable'
            results['probe_kept_ingress'] = probed['active_generation'] == state['active_generation'] > 0
        else:
            results['unavailable_reported'] = state['egress_state'] == 'unavailable'
        results['ingress_retained'] = state['active_generation'] > 0
    finally:
        if relay_fault:
            subprocess.run(['systemctl', 'start', 'tempest-lab-awg-relay.service'], check=True, timeout=20)
            recovered = api.request('POST', '/core/awg-egresses/probe', token=token)
            results['transport_recovered'] = recovered['egress_state'] == 'active_verified'
        else:
            recovered = api.request('POST', '/core/recover', token=token)
        results['lkg_recovered'] = recovered['active_generation'] == recovered['last_known_good_generation'] > 0
        results['revocations_preserved'] = original_revoked <= revoked('/var/lib/tempest-xray-agent/xray.json')
    for name, ok in results.items():
        print(name, 'PASS' if ok else 'FAIL', flush=True)
    raise SystemExit(0 if results and all(results.values()) else 1)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Lab fault test did not complete; inspect active generation before further operations', flush=True)
        raise SystemExit(1) from None
