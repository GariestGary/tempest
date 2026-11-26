#!/usr/bin/env python3
"""Exercise the existing lab WG user and AWG bridge without exposing addresses."""
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import sys
import time

UUID = 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609'


def read_exact(sock, length):
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError('unexpected end of stream')
        data += chunk
    return data


def https(socks=False):
    for _ in range(3):
        try:
            if socks:
                with open('/run/tempest-xray/active.json') as file:
                    config = json.load(file)
                server = next(x for x in config['outbounds'] if x['tag'] == 'vpn')['settings']['servers'][0]
                username = server['users'][0]['user'].encode()
                password = server['users'][0]['pass'].encode()
                connection = socket.create_connection(('127.0.0.1', server['port']), timeout=6)
                connection.settimeout(6)
                connection.sendall(b'\x05\x01\x02')
                if read_exact(connection, 2) != b'\x05\x02':
                    raise OSError('SOCKS authentication unavailable')
                connection.sendall(b'\x01' + bytes([len(username)]) + username + bytes([len(password)]) + password)
                if read_exact(connection, 2) != b'\x01\x00':
                    raise OSError('SOCKS authentication failed')
                connection.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('1.1.1.1') + struct.pack('!H', 443))
                header = read_exact(connection, 4)
                if header[1] != 0 or header[3] not in (1, 4):
                    raise OSError('SOCKS connect failed')
                read_exact(connection, (4 if header[3] == 1 else 16) + 2)
            else:
                connection = socket.create_connection(('1.1.1.1', 443), timeout=6)
            with ssl.create_default_context().wrap_socket(connection, server_hostname='cloudflare-dns.com') as tls:
                tls.settimeout(6)
                tls.sendall(b'GET /cdn-cgi/trace HTTP/1.1\r\nHost: cloudflare-dns.com\r\nConnection: close\r\n\r\n')
                result = b''
                while len(result) < 32768:
                    part = tls.recv(4096)
                    if not part:
                        break
                    result += part
            match = re.search(rb'(?m)^ip=([^\s]+)$', result)
            if not result.startswith(b'HTTP/1.1 200') or not match:
                raise ValueError('missing response')
            observed = ipaddress.ip_address(match.group(1).decode())
            if observed.version != 4 or not observed.is_global:
                raise ValueError('missing global IPv4')
            return str(observed)
        except (OSError, ValueError, KeyError, StopIteration, ssl.SSLError):
            time.sleep(0.3)
    return None


def dns(transport):
    result = subprocess.run(['dig', '-r', '@198.51.100.53', 'one.one.one.one', 'A', '+short',
                             '+timeout=4', '+tries=2', transport], capture_output=True, text=True, timeout=12)
    try:
        return result.returncode == 0 and any(ipaddress.ip_address(x).version == 4 for x in result.stdout.split())
    except ValueError:
        return False


def udp_ntp():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(6)
            client.sendto(b'\x1b' + b'\0' * 47, ('162.159.200.1', 123))
            data, _ = client.recvfrom(512)
            return len(data) >= 48 and data[0] & 7 in (4, 5)
    except OSError:
        return False


def ipv6_controls():
    def trial(address, want_response):
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as client:
                client.settimeout(3)
                client.connect((address, 18080))
                client.sendall(b'TPAB-IPV6-CONTROL\n')
                return bool(client.recv(512)) == want_response
        except OSError:
            return not want_response
    return trial('2001:db8:2::10', True) and trial('2001:db8:2::20', False)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == 'user':
        from xray_mvp_probe_checks import fixture_controls
        print(json.dumps({'ip': https(), 'dns_tcp': dns('+tcp'), 'dns_udp': dns('+notcp'),
                          'udp_ntp': udp_ntp(), 'direct_block': fixture_controls(), 'ipv6': ipv6_controls()}))
        return
    if len(sys.argv) == 2 and sys.argv[1] == 'socks':
        print(json.dumps({'ip': https(socks=True)}))
        return
    if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != UUID:
        raise SystemExit('Wrong VM or UID')
    path = '/opt/tempest/bin/working-core-awg-traffic'
    user = subprocess.run(['ip', 'netns', 'exec', 'tpab-w', '/usr/bin/python3', path, 'user'],
                          capture_output=True, text=True, timeout=80, check=True)
    bridge = subprocess.run(['ip', 'netns', 'exec', 'tpab-g', 'setpriv', '--reuid=tempest-xray',
                             '--regid=tempest-xray', '--clear-groups', '/usr/bin/python3', path, 'socks'],
                            capture_output=True, text=True, timeout=30, check=True)
    observed, expected = json.loads(user.stdout), json.loads(bridge.stdout)
    results = {name: observed[name] for name in ('dns_tcp', 'dns_udp', 'udp_ntp', 'direct_block', 'ipv6')}
    results['https'] = bool(observed['ip'])
    results['egress_ip_match'] = bool(observed['ip'] and observed['ip'] == expected['ip'])
    for name, ok in results.items():
        print(name, 'PASS' if ok else 'FAIL', flush=True)
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Lab traffic check could not complete', flush=True)
        raise SystemExit(1) from None
