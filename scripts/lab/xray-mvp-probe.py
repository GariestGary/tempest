#!/usr/bin/env python3
"""Probe the fixed tpab WireGuard/egress lab fixture without network mutation."""

import socket
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor

def check(address, wire, source):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    kind = socket.SOCK_DGRAM if wire == "udp" else socket.SOCK_STREAM
    with socket.socket(family, kind) as client:
        client.settimeout(1.4)
        mark = b"TPAB-PATH-" + uuid.uuid4().hex.encode() + b"\n"
        try:
            client.connect((address, 18080))
            client.sendall(mark)
            if wire == "udp":
                response = client.recv(1024)
            else:
                with client.makefile("rb") as stream:
                    response = stream.readline() + stream.readline().rstrip(b"\n")
        except OSError:
            response = b""
    return response == (mark + source.encode() if source else b"")

def dns(address, wire, family, expected):
    args = ["dig", "-r", "@" + address, uuid.uuid4().hex + ".poc.test",
            family, "+short", "+norecurse", "+timeout=1", "+tries=1", wire]
    result = subprocess.run(args, capture_output=True, text=True, timeout=2)
    return result.returncode == 0 and result.stdout.strip() == expected

with ThreadPoolExecutor(12) as pool:
    checks = [
        pool.submit(check, address, wire, source)
        for address, source in (
            ("198.51.100.10", "203.0.113.1"), ("2001:db8:2::10", "2001:db8:10::1"),
            ("198.51.100.20", "203.0.113.5"), ("2001:db8:2::20", "2001:db8:11::1"),
            ("198.51.100.30", ""), ("2001:db8:2::30", ""),
        ) for wire in ("tcp", "udp")
    ] + [
        pool.submit(dns, "198.51.100.53", "+tcp", "A", "198.51.100.20"),
        pool.submit(dns, "2001:db8:2::53", "+notcp", "AAAA", "2001:db8:2::20"),
    ]
    outcomes = [result.result() for result in checks]
print(f"Xray traffic probe {sum(outcomes)}/{len(outcomes)}", flush=True)
raise SystemExit(0 if all(outcomes) else 1)
