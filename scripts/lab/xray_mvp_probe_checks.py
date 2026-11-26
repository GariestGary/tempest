"""Positive DIRECT and negative BLOCK controls for the disposable tpab lab."""
import socket
import uuid


def fixture_controls():
    for address, expected in (('198.51.100.10', '203.0.113.1'), ('198.51.100.30', None)):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            marker = b'TPAB-AWG-' + uuid.uuid4().hex.encode() + b'\n'
            try:
                with socket.socket(socket.AF_INET, kind) as client:
                    client.settimeout(1.2)
                    client.connect((address, 18080))
                    client.sendall(marker)
                    if kind == socket.SOCK_STREAM:
                        with client.makefile('rb') as stream:
                            received = stream.readline() + stream.readline().rstrip(b'\n')
                    else:
                        received = client.recv(1024)
            except OSError:
                received = b''
            if expected is None:
                if received:
                    return False
            elif received != marker + expected.encode():
                return False
    return True
