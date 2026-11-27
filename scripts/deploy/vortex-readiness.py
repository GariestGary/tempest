#!/usr/bin/env python3
"""Read-only collision report for the proposed TEMPEST instance; no sockets are bound."""
import grp
import ipaddress
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

SUBNETS = {'compose': '10.244.247.0/24', 'clients': '10.245.248.0/24'}
PORTS = {'panel_tcp': ('tcp', 9080), 'ingress_udp': ('udp', 51820)}
PATHS = ('/opt/tempest', '/etc/tempest/xray-agent.env', '/var/lib/tempest-xray-agent', '/var/lib/tempest-core-private', '/run/netns/tempest-ns-xray', '/run/netns/tempest-ns-xray-probe')
UNITS = ('tempest-xray-agent.service', 'tempest-xray.service', 'tempest-xray-probe.service', 'tempest-awgproxy.service', 'tempest-awg-dns.service')
USERS = ('tempest-xray-agent', 'tempest-xray', 'tempest-xray-probe', 'tempest-awgproxy')
GROUPS = ('tempest-xray', 'tempest-control')


def run(*args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f'cannot inspect {args[0]}') from exc


def networks():
    result = []
    for family in ('-4', '-6'):
        for obj in json.loads(run('ip', '-j', family, 'route', 'show', 'table', 'all')):
            dst = obj.get('dst', 'default')
            if dst != 'default':
                try:
                    result.append(('route', ipaddress.ip_network(dst, strict=False)))
                except ValueError:
                    pass
    for link in json.loads(run('ip', '-j', 'address', 'show')):
        for address in link.get('addr_info', []):
            if address.get('family') in ('inet', 'inet6'):
                result.append(('address', ipaddress.ip_network(f"{address['local']}/{address['prefixlen']}", strict=False)))
    ids = run('docker', 'network', 'ls', '-q').split()
    if ids:
        for item in json.loads(run('docker', 'network', 'inspect', *ids)):
            for config in item.get('IPAM', {}).get('Config') or []:
                if config.get('Subnet'):
                    result.append(('docker:' + item['Name'], ipaddress.ip_network(config['Subnet'], strict=False)))
    return result


def socket_conflicts(text, ports=PORTS):
    busy = {name: [] for name in ports}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise RuntimeError('cannot parse ss output')
        family, local = fields[0], fields[4]
        try:
            port = int(local.rsplit(':', 1)[1])
        except (IndexError, ValueError):
            continue
        for name, (protocol, candidate) in ports.items():
            # Conservatively reject every bind to a proposed port in the protocol.
            if family.startswith(protocol) and port == candidate:
                busy[name].append(local)
    return busy


def present(getter, names):
    found = []
    for name in names:
        try:
            getter(name)
        except KeyError:
            continue
        found.append(name)
    return found


def main():
    observed = networks()
    subnets = {}
    for name, value in SUBNETS.items():
        network = ipaddress.ip_network(value)
        subnets[name] = {'proposed': value, 'overlaps': sorted({f'{origin}:{existing}' for origin, existing in observed if existing.version == network.version and existing.overlaps(network)})}
    sockets = socket_conflicts(run('ss', '-H', '-lntu'))
    paths = [p for p in PATHS if os.path.lexists(p)]
    units = []
    for unit in UNITS:
        state = run('systemctl', 'show', unit, '--property=LoadState', '--value').strip()
        if state != 'not-found':
            units.append(f'{unit}:{state}')
    namespaces = [line.split()[0] for line in run('ip', 'netns', 'list').splitlines() if line.split()]
    users = present(pwd.getpwnam, USERS)
    groups = present(grp.getgrnam, GROUPS)
    docker_names = run('docker', 'network', 'ls', '--format', '{{.Name}}').splitlines()
    network_name_collision = 'tempest-working-core_default' in docker_names
    containers = run('docker', 'ps', '-a', '--format', '{{.Names}}').splitlines()
    volumes = run('docker', 'volume', 'ls', '--format', '{{.Name}}').splitlines()
    docker_objects = [n for n in ('tempest-working-core-api-1', 'tempest-working-core-panel-1') if n in containers]
    if 'tempest-working-core_core_db' in volumes:
        docker_objects.append('tempest-working-core_core_db')
    result = {'machine': os.uname().machine, 'subnets': subnets, 'ports_in_use': sockets, 'existing_paths': paths, 'protected_existing_parent': '/etc/tempest' if os.path.lexists('/etc/tempest') else None, 'existing_units': units, 'existing_users': users, 'existing_groups': groups, 'existing_target_namespaces': [n for n in namespaces if n in ('tempest-ns-xray', 'tempest-ns-xray-probe')], 'compose_network_name_collision': network_name_collision, 'root_free_bytes': shutil.disk_usage('/').free,
              'existing_docker_objects': docker_objects,
              'firewall_assessed': False, 'ingress_probe_topology_validated': False,
              'scope': 'collision snapshot only; success does not authorize or validate installation; state may change'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    collision = any(v['overlaps'] for v in subnets.values()) or any(sockets.values()) or paths or units or users or groups or result['existing_target_namespaces'] or network_name_collision or docker_objects or result['machine'] != 'x86_64'
    return 1 if collision else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f'INCOMPLETE: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
