#!/usr/bin/env python3
"""Fail-closed production-probe candidate; requires a separately provisioned WG/control fixture.

No network mutation and no secrets in output. This file is NOT installed by the
binary installers; its network topology must be reviewed and verified first.
"""
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import uuid

CONFIG = Path('/etc/tempest/working-core-probe.json')
DOMAIN = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$')
IFACE = re.compile(r'^[A-Za-z0-9_.-]{1,15}$')
TIMEOUT = 1.2


def ip(value):
    result = ipaddress.ip_address(value)
    if result.version != 4 or result.is_unspecified or result.is_loopback or result.is_multicast or result.is_link_local:
        raise ValueError('invalid IPv4 address')
    if any(result in ipaddress.ip_network(net) for net in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')):
        raise ValueError('documentation/laboratory IPv4 address forbidden')
    return str(result)


def endpoint(value, needs_source=True):
    required = {'address', 'port'} | ({'expected_source'} if needs_source else set())
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('invalid fixture endpoint')
    address = ip(value['address'])
    port = value['port']
    if type(port) is not int or not 1 <= port <= 65535 or port in (53, 853):
        raise ValueError('invalid fixture port')
    result = {'address': address, 'port': port}
    if needs_source:
        result['expected_source'] = ip(value['expected_source'])
    return result


def parse_config(config):
    keys = {'wg_interface', 'wg_source', 'control_interface', 'control_source', 'control_expected_source',
            'dns_server', 'dns_name', 'https_address', 'https_name', 'direct', 'vpn', 'block'}
    optional = {'vpn_udp_stun', 'https_expected_source'}
    if not isinstance(config, dict) or not keys <= set(config) <= keys | optional:
        raise ValueError('invalid probe configuration fields')
    result = dict(config)
    result.setdefault('vpn_udp_stun', False)
    if type(result['vpn_udp_stun']) is not bool:
        raise ValueError('invalid STUN mode')
    for key in ('wg_interface', 'control_interface'):
        if not isinstance(result[key], str) or not IFACE.fullmatch(result[key]):
            raise ValueError('invalid interface')
    if result['wg_interface'] == result['control_interface']:
        raise ValueError('control and WG interfaces must differ')
    for key in ('wg_source', 'control_source', 'control_expected_source', 'dns_server', 'https_address'):
        result[key] = ip(result[key])
    if result['wg_source'] == result['control_source']:
        raise ValueError('control and WG sources must differ')
    for key in ('dns_name', 'https_name'):
        if not isinstance(result[key], str) or not DOMAIN.fullmatch(result[key]) or len(result[key]) > 253:
            raise ValueError('invalid DNS name')
    for key in ('direct', 'vpn', 'block'):
        result[key] = endpoint(result[key], key != 'block')
    if not ipaddress.ip_address(result['https_address']).is_global or not ipaddress.ip_address(result['vpn']['expected_source']).is_global:
        raise ValueError('HTTPS and VPN source must be global IPv4')
    if len({(result[k]['address'], result[k]['port']) for k in ('direct', 'vpn', 'block')}) != 3:
        raise ValueError('fixture selectors must differ')
    if 'https_expected_source' in result:
        result['https_expected_source'] = ip(result['https_expected_source'])
        if not ipaddress.ip_address(result['https_expected_source']).is_global:
            raise ValueError('HTTPS source must be global IPv4')
    return result


def load_config(path=CONFIG):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('configuration must be root-owned and protected')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            content = stream.read(32769)
        if len(content) > 32768:
            raise ValueError('configuration too large')
        return parse_config(json.loads(content))
    finally:
        os.close(fd)


def route_is(address, source, interface):
    try:
        p = subprocess.run(['/usr/sbin/ip', '-j', '-4', 'route', 'get', address, 'from', source],
                           capture_output=True, text=True, timeout=1, check=True)
        routes = json.loads(p.stdout)
        return len(routes) == 1 and routes[0].get('dev') == interface
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def echo(target, source, interface, protocol, expected, *, blocked=False):
    if not route_is(target['address'], source, interface):
        return False
    kind = socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM
    marker = ('TEMPEST-' + uuid.uuid4().hex + '\n').encode()
    try:
        with socket.socket(socket.AF_INET, kind) as client:
            client.settimeout(TIMEOUT)
            client.bind((source, 0))
            client.connect((target['address'], target['port']))
            client.sendall(marker)
            if protocol == 'tcp':
                with client.makefile('rb') as stream:
                    response = stream.readline(256) + stream.readline(128).rstrip(b'\n')
            else:
                response = client.recv(512)
        return not blocked and response == marker + expected.encode()
    except socket.timeout:
        return blocked
    except OSError:
        # Refused/unreachable cannot prove a live service was blocked.
        return False


def dns(config, transport, dnskey=False):
    if not route_is(config['dns_server'], config['wg_source'], config['wg_interface']):
        return False
    query = '.' if dnskey else config['dns_name']
    kind = 'DNSKEY' if dnskey else 'A'
    flags = ['+noall', '+comments', '+answer', '+stats'] if dnskey else ['+short']
    args = ['/usr/bin/dig', '-r', '-b', config['wg_source'], '@' + config['dns_server'], query, kind,
            transport, '+dnssec', '+timeout=2', '+tries=1', *flags]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=3, check=True)
        if not dnskey:
            return any(ipaddress.ip_address(line).version == 4 for line in proc.stdout.split())
        response_flags = re.search(r'flags:\s*([^;]+);', proc.stdout)
        size = re.search(r'MSG SIZE\s+rcvd:\s*(\d+)', proc.stdout)
        return bool('status: NOERROR' in proc.stdout and re.search(r'\bDNSKEY\b', proc.stdout)
                    and response_flags and 'tc' not in response_flags.group(1).split()
                    and size and int(size.group(1)) > 512)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def https(config):
    if not route_is(config['https_address'], config['wg_source'], config['wg_interface']):
        return False
    try:
        proc = subprocess.run(['/usr/bin/curl', '-fsS', '--noproxy', '*', '--interface', config['wg_source'],
                               '--connect-timeout', '2', '--max-time', '3', '--resolve',
                               f"{config['https_name']}:443:{config['https_address']}",
                               f"https://{config['https_name']}/cdn-cgi/trace"],
                              capture_output=True, text=True, timeout=4, check=True)
        match = re.search(r'^ip=([^\s]+)$', proc.stdout, re.MULTILINE)
        return bool(match and ip(match.group(1)) == config.get('https_expected_source', config['vpn']['expected_source']))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def stun(config):
    target = config['vpn']
    source = config['wg_source']

    if not route_is(target['address'], source, config['wg_interface']):
        return False

    cookie = 0x2112A442
    tx = os.urandom(12)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            client.bind((source, 0))
            client.connect((target['address'], target['port']))
            client.send(struct.pack('!HHI12s', 1, 0, cookie, tx))
            data = client.recv(2048)

        if len(data) < 20:
            return False

        kind, length, magic, reply = struct.unpack_from('!HHI12s', data)

        if (kind != 0x0101 or magic != cookie or reply != tx
                or len(data) != 20 + length):
            return False

        pos = 20
        while pos + 4 <= len(data):
            attr, size = struct.unpack_from('!HH', data, pos)
            value = data[pos+4:pos+4+size]

            if len(value) != size:
                return False

            if attr == 0x0020 and size == 8 and value[1] == 1:
                raw = struct.unpack('!I', value[4:8])[0] ^ cookie
                observed = str(ipaddress.IPv4Address(raw))
                return observed == target['expected_source']

            pos += 4 + ((size + 3) & ~3)

    except (OSError, ValueError, struct.error):
        return False

    return False


def run(config):
    c = config
    # Positive checks of the exact BLOCK target over a separately routed control
    # interface are mandatory, before and after the negative WG checks.
    def witness(protocol):
        return echo(c['block'], c['control_source'], c['control_interface'], protocol, c['control_expected_source'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        before = list(pool.map(witness, ('tcp', 'udp')))
    if not all(before):
        print('probe: FAIL (independent BLOCK control unavailable)', flush=True)
        return False
    jobs = {
        'direct_tcp': lambda: echo(c['direct'], c['wg_source'], c['wg_interface'], 'tcp', c['direct']['expected_source']),
        'direct_udp': lambda: echo(c['direct'], c['wg_source'], c['wg_interface'], 'udp', c['direct']['expected_source']),
        'vpn_tcp': lambda: echo(c['vpn'], c['wg_source'], c['wg_interface'], 'tcp', c['vpn']['expected_source']),
        'vpn_udp': lambda: echo(c['vpn'], c['wg_source'], c['wg_interface'], 'udp', c['vpn']['expected_source']),
        'block_tcp': lambda: echo(c['block'], c['wg_source'], c['wg_interface'], 'tcp', '', blocked=True),
        'block_udp': lambda: echo(c['block'], c['wg_source'], c['wg_interface'], 'udp', '', blocked=True),
        'dns_tcp': lambda: dns(c, '+tcp'), 'dns_udp': lambda: dns(c, '+notcp'),
        'dnskey_tcp': lambda: dns(c, '+tcp', True), 'https': lambda: https(c),
    }
    if c.get('vpn_udp_stun'):
        del jobs['vpn_tcp']  # HTTPS independently verifies VPN TCP.
        jobs['vpn_udp'] = lambda: stun(c)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(action) for name, action in jobs.items()}
        results = {name: future.result() for name, future in futures.items()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        after = list(pool.map(witness, ('tcp', 'udp')))
    results['independent_block_control'] = all(after)
    print('probe:', ' '.join(f'{name}={"pass" if result else "fail"}' for name, result in results.items()), flush=True)
    return all(results.values())


if __name__ == '__main__':
    try:
        sys.exit(0 if run(load_config()) else 1)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print('probe: FAIL (invalid or unavailable protected configuration)', flush=True)
        sys.exit(1)
