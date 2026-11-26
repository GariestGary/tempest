#!/usr/bin/env python3
"""Exercise pending AWG rollback while preserving the durable revocation set."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

VM = 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609'
JOURNAL = Path('/var/lib/tempest-xray-agent/xray.json')
PLAN = Path('/run/tempest-xray-agent/awg-rollback-test.json')


def call(method, argument=None):
    command = ['setpriv', '--reuid=10001', '--regid=986', '--clear-groups', 'env',
               'PYTHONPATH=/opt/tempest/backend', '/opt/tempest/backend/.venv/bin/python',
               '-m', 'app.network_agent.xray_client', method]
    if argument:
        command.append(str(argument))
    result = subprocess.run(command, cwd='/tmp', capture_output=True, text=True, timeout=125, check=True)
    response = json.loads(result.stdout)
    if not response['ok']:
        raise RuntimeError('Agent rejected test')
    return response['result']


def main():
    if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != VM:
        raise SystemExit('Wrong VM or UID')
    if PLAN.exists():
        raise SystemExit('Test plan path already exists')
    record = json.loads(JOURNAL.read_text())
    if record['pending'] or not record['lkg'] or not record['lkg']['plan'].get('awg_config'):
        raise SystemExit('Expected confirmed AWG and no pending transaction')
    before = set(record['revoked'])
    candidate = record['lkg']['plan']
    generation_id = str(uuid4())
    candidate.update(generation_id=generation_id, base_generation=candidate['generation'],
                     generation=record['high_water'] + 1, default_egress='block')
    fd = os.open(PLAN, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        os.fchown(fd, 0, 986)
        with os.fdopen(fd, 'w') as file:
            json.dump(candidate, file)
        call('stage', PLAN)
        call('apply', generation_id)
        status = call('verify', generation_id)
        assert status['pending_generation_id'] == generation_id
        status = call('rollback', generation_id)
        after = json.loads(JOURNAL.read_text())
        peers = {peer['public_key'] for peer in after['lkg']['plan']['peers']}
        results = {'rollback_lkg': status['active_generation'] == status['last_known_good_generation'] > 0,
                   'pending_cleared': status.get('pending_generation_id') is None,
                   'revocations_preserved': before <= set(after['revoked']),
                   'revoked_peer_not_restored': not peers.intersection(after['revoked'])}
        for name, ok in results.items():
            print(name, 'PASS' if ok else 'FAIL', flush=True)
        if not all(results.values()):
            raise SystemExit(1)
    finally:
        PLAN.unlink(missing_ok=True)
        # Only this transaction may be rolled back. No copied journal is installed.
        try:
            status = call('read_status')
            if status.get('pending_generation_id') == generation_id:
                call('rollback', generation_id)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            print('Could not reconcile test transaction; inspect Agent status', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Lab rollback test did not complete; inspect Agent status', flush=True)
        raise SystemExit(1) from None
