#!/usr/bin/env python3
"""Inspect the approved lab's runtime boundaries without printing secrets."""
import configparser
import json
import os
from pathlib import Path
import subprocess

VM = 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609'


def status(unit):
    pid = int(subprocess.check_output(['systemctl', 'show', unit, '--property=MainPID', '--value'], text=True))
    if pid <= 0:
        raise ValueError('unit not running')
    values = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
    return pid, values


def main():
    if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != VM:
        raise SystemExit('Wrong VM or UID')
    units = ('tempest-xray-agent.service', 'tempest-xray.service', 'tempest-awgproxy.service')
    processes = [status(unit) for unit in units]
    ids = [value['Uid'].split()[0] for _, value in processes]
    capabilities = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
    results = {
        'separate_uids': len(set(ids)) == 3,
        'no_capabilities': all(int(value[key].strip(), 16) == 0 for _, value in processes for key in capabilities),
        'no_new_privileges': all(value['NoNewPrivs'].strip() == '1' for _, value in processes),
        'isolated_namespace': all(os.path.samestat(os.stat(f'/proc/{pid}/ns/net'), os.stat('/run/netns/tpab-g'))
                                  for pid, _ in processes)
                              and not os.path.samestat(os.stat('/run/netns/tpab-g'), os.stat('/proc/1/ns/net')),
        'private_journal': (Path('/var/lib/tempest-xray-agent/xray.json').stat().st_mode & 0o777) == 0o600,
        'private_runtime': (Path('/run/tempest-xray/active-awg.conf').stat().st_mode & 0o777) == 0o640,
    }
    containers = json.loads(subprocess.check_output(['docker', 'inspect', 'tempest-working-core-api-1', 'tempest-working-core-panel-1'], text=True))
    results['containers_isolated'] = all(
        not c['HostConfig']['Privileged'] and c['HostConfig']['NetworkMode'] != 'host'
        and c['HostConfig']['PidMode'] != 'host' and 'ALL' in c['HostConfig']['CapDrop']
        and c['HostConfig']['ReadonlyRootfs']
        and all(m['Source'] not in ('/run/docker.sock', '/var/run/docker.sock') for m in c['Mounts'])
        for c in containers
    )
    config = configparser.ConfigParser(interpolation=None)
    config.read('/run/tempest-xray/active-awg.conf')
    secrets = [config[section][key] for section, key in (
        ('Interface', 'privatekey'), ('Interface', 'headerprotectionkey'),
        ('Peer', 'presharedkey'), ('Peer', 'endpoint'), ('Socks5', 'password'),
    ) if key in config[section]]
    logs = subprocess.check_output(['journalctl', '-u', units[0], '-u', units[1], '-u', units[2],
                                    '-u', 'tempest-lab-awg-relay.service', '--since', '24 hours ago', '--no-pager'])
    api_logs = subprocess.run(['docker', 'logs', 'tempest-working-core-api-1'], capture_output=True)
    logs += api_logs.stdout + api_logs.stderr
    results['secrets_absent_in_logs'] = all(value.encode() not in logs for value in secrets)
    for name, ok in results.items():
        print(name, 'PASS' if ok else 'FAIL', flush=True)
    if not all(results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Lab isolation check did not complete', flush=True)
        raise SystemExit(1) from None
