#!/usr/bin/env python3
"""Bounded test fixture for a separately approved, controlled probe endpoint.

Not a production gateway. Binding is operator-selected; never expose a wildcard.
"""
import argparse
import asyncio
import ipaddress
import re
import sys

MARKER = re.compile(rb'^TEMPEST-[0-9a-f]{32}\n$')


def response(data, peer, allowed):
    if not MARKER.fullmatch(data):
        return b''
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return b''
    if address.version != 4 or address not in allowed:
        return b''
    return data + str(address).encode()


class UdpEcho(asyncio.DatagramProtocol):
    def __init__(self, allowed):
        self.allowed = allowed
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, peer):
        result = response(data, peer[0], self.allowed)
        if result:
            self.transport.sendto(result, peer)


async def tcp_echo(reader, writer, allowed):
    try:
        data = await asyncio.wait_for(reader.readline(), timeout=2)
        peer = writer.get_extra_info('peername')
        result = response(data, peer[0], allowed) if peer else b''
        if result:
            writer.write(result + b'\n')
            await asyncio.wait_for(writer.drain(), timeout=2)
    except (asyncio.TimeoutError, OSError, ValueError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def serve(address, port, allowed):
    loop = asyncio.get_running_loop()
    tcp = await asyncio.start_server(lambda r, w: tcp_echo(r, w, allowed), address, port, limit=256)
    try:
        udp, _ = await loop.create_datagram_endpoint(lambda: UdpEcho(allowed), local_addr=(address, port))
        try:
            print('fixture ready', flush=True)
            await asyncio.Event().wait()
        finally:
            udp.close()
    finally:
        tcp.close()
        await tcp.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--allow', action='append', required=True, help='single IPv4 source, repeatable')
    args = parser.parse_args()
    listen = ipaddress.ip_address(args.listen)
    allowed = {ipaddress.ip_address(item) for item in args.allow}
    if (listen.version != 4 or listen.is_unspecified or listen.is_multicast or not 1024 <= args.port <= 65535
            or not allowed or any(a.version != 4 or a.is_unspecified or a.is_multicast for a in allowed)):
        raise ValueError('invalid listen address, source or port')
    asyncio.run(serve(str(listen), args.port, allowed))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError):
        print('fixture: FAIL (invalid address or unavailable listener)', file=sys.stderr)
        sys.exit(1)
