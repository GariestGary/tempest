#!/usr/bin/env python3
"""Controlled echo and authoritative DNS sink for the tpab-s lab namespace."""
import asyncio
import ipaddress
import socket
import struct


def dns_reply(data):
    if len(data) < 17 or data[4:6] != b'\x00\x01':
        return b''
    pos = 12
    while pos < len(data) and data[pos]:
        n = data[pos]
        if n > 63 or pos + n + 1 >= len(data):
            return b''
        pos += n + 1
    pos += 1
    if pos + 4 > len(data):
        return b''
    qtype, qclass = struct.unpack('!HH', data[pos:pos + 4])
    question = data[12:pos + 4]
    if qclass != 1 or qtype not in (1, 28):
        return data[:2] + b'\x81\x80' + b'\x00\x01\x00\x00\x00\x00\x00\x00' + question
    address = ipaddress.ip_address('198.51.100.20' if qtype == 1 else '2001:db8:2::20').packed
    answer = b'\xc0\x0c' + struct.pack('!HHIH', qtype, 1, 30, len(address)) + address
    return data[:2] + b'\x81\x80' + b'\x00\x01\x00\x01\x00\x00\x00\x00' + question + answer


class UDP(asyncio.DatagramProtocol):
    def __init__(self, dns):
        self.dns = dns

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, peer):
        reply = dns_reply(data) if self.dns else data + peer[0].encode()
        if reply:
            self.transport.sendto(reply, peer)
            print('udp', self.transport.get_extra_info('sockname'), peer[0], flush=True)


async def tcp(reader, writer, dns):
    try:
        peer = writer.get_extra_info('peername')
        if dns:
            n = struct.unpack('!H', await reader.readexactly(2))[0]
            reply = dns_reply(await reader.readexactly(n))
            if reply:
                writer.write(struct.pack('!H', len(reply)) + reply)
        else:
            while data := await asyncio.wait_for(reader.readline(), 60):
                writer.write(data + peer[0].encode() + b'\n')
                await writer.drain()
        await writer.drain()
        print('tcp', writer.get_extra_info('sockname'), peer[0], flush=True)
    except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    loop = asyncio.get_running_loop()
    servers, transports = [], []
    for base in ('198.51.100.', '2001:db8:2::'):
        for suffix, port, dns in [(s, 18080, False) for s in ('10', '20', '30')] + [('53', 53, True), ('10', 53, False), ('10', 853, False)]:
            address = base + suffix
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            server = await asyncio.start_server(lambda r, w, d=dns: tcp(r, w, d), address, port, family=family)
            transport, _ = await loop.create_datagram_endpoint(lambda d=dns: UDP(d), local_addr=(address, port), family=family)
            servers.append(server)
            transports.append(transport)
    print('sink ready', flush=True)
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
