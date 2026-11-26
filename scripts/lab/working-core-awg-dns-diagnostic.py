#!/usr/bin/env python3
"""Compare TCP DNS over the active local SOCKS bridge without printing secrets."""
import configparser
import json
import os
import socket
import ssl
import struct


def read_exact(sock, count):
    data = b''
    while len(data) < count:
        piece = sock.recv(count - len(data))
        if not piece:
            raise OSError('unexpected end of stream')
        data += piece
    return data


step = 'configuration'
try:
    with open('/run/tempest-xray/active.json') as file:
        config = json.load(file)
    server = next(out for out in config['outbounds'] if out.get('tag') == 'vpn')['settings']['servers'][0]
    user = server['users'][0]['user'].encode()
    password = server['users'][0]['pass'].encode()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read('/run/tempest-xray/active-awg.conf')
    dns = os.environ.get('TEMPEST_LAB_TEST_DNS') or parser['Interface']['dns'].split(',')[0].strip()
    query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03one\x03one\x03one\x00\x00\x01\x00\x01'
    step = 'connect proxy'
    with socket.create_connection(('127.0.0.1', server['port']), timeout=5) as client:
        client.settimeout(5)
        step = 'authenticate proxy'
        client.sendall(b'\x05\x01\x02')
        if read_exact(client, 2) != b'\x05\x02':
            raise OSError('SOCKS auth method')
        client.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
        if read_exact(client, 2) != b'\x01\x00':
            raise OSError('SOCKS auth failure')
        step = 'connect resolver'
        port = int(os.environ.get('TEMPEST_LAB_TEST_DNS_PORT', '53'))
        client.sendall(b'\x05\x01\x00\x01' + socket.inet_aton(dns) + struct.pack('!H', port))
        reply = read_exact(client, 4)
        if reply[1] != 0:
            print('SOCKS resolver connect reply:', reply[1])
            raise OSError('SOCKS connection failure')
        lengths = {1: 4, 4: 16}
        address_length = lengths.get(reply[3])
        if address_length is None:
            raise OSError('SOCKS address type')
        read_exact(client, address_length + 2)
        if os.environ.get('TEMPEST_LAB_TEST_DNS_TLS'):
            step = 'TLS handshake'
            client = ssl.create_default_context().wrap_socket(client, server_hostname='one.one.one.one')
        step = 'query resolver'
        client.sendall(struct.pack('!H', len(query)) + query)
        length = struct.unpack('!H', read_exact(client, 2))[0]
        answer = read_exact(client, length)
        print('Direct SOCKS TCP DNS:', 'pass' if answer[:2] == query[:2] and len(answer) >= 12 else 'fail')
except (OSError, KeyError, ValueError, StopIteration, struct.error) as error:
    print('Direct SOCKS TCP DNS: fail at', step, 'kind', type(error).__name__)
    raise SystemExit(1) from None
