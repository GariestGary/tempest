#!/usr/bin/env python3
"""Generate fresh VORTEX deployment credentials without printing secrets."""

import grp
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

if os.geteuid() != 0 or socket.gethostname() != "vortex":
    sys.exit("Wrong host or UID")

if len(sys.argv) != 3:
    sys.exit("Usage: provision-working-core.py HTTPS_IP STUN_IP")

https_ip, stun_ip = map(ipaddress.IPv4Address, sys.argv[1:])

if not https_ip.is_global or not stun_ip.is_global:
    sys.exit("Expected public VPN IPv4 addresses")

control_gid = grp.getgrnam("tempest-control").gr_gid
probe_gid = grp.getgrnam("tempest-xray-probe").gr_gid

etc = Path("/etc/tempest")
private = Path("/var/lib/tempest-core-private")

if etc.is_symlink() or not etc.is_dir():
    sys.exit("Invalid /etc/tempest directory")

targets = (
    private,
    etc / "working-core-probe.key",
    etc / "working-core-gateway.pub",
    etc / "working-core-probe.json",
    etc / "xray-agent.env",
)

for target in targets:
    if target.exists() or target.is_symlink():
        sys.exit(f"Refusing overwrite: {target}")

os.umask(0o077)

def generate_key():
    return subprocess.check_output(
        ["/usr/bin/wg", "genkey"], text=True
    ).strip()

def public_key(key):
    return subprocess.check_output(
        ["/usr/bin/wg", "pubkey"],
        input=key + "\n", text=True
    ).strip()

gateway_key = generate_key()
probe_key = generate_key()

rules = [
    {
        "destination": address + "/32",
        "port": port,
        "egress": egress,
    }
    for address, port, egress in (
        ("10.245.249.1", 18080, "direct"),
        ("10.245.249.1", 18081, "block"),
        ("1.1.1.1", 443, "vpn"),
        ("162.159.207.0", 3478, "vpn"),
    )
]

deployment = {
    "target_namespace_id": "tempest-ns-xray",
    "listen_address": "10.245.249.2",
    "listen_port": 51820,
    "client_endpoint_address": "192.168.1.66",
    "client_endpoint_port": 51820,
    "tunnel_addresses": [
        "10.245.248.1/32",
        "fd42:245:248::1/128",
    ],
    "client_pools": [
        "10.245.248.0/24",
        "fd42:245:248::/64",
    ],
    "private_key": gateway_key,
    "probe": {
        "peer": {
            "public_key": public_key(probe_key),
            "addresses": [
                "10.245.248.2/32",
                "fd42:245:248::2/128",
            ],
        },
        "rules": rules,
    },
    "resolvers": ["1.1.1.1"],
    "awg_dns": {
        "address": "1.1.1.1",
        "server_name": "cloudflare-dns.com",
        "port": 853,
    },
}

probe = {
    "wg_interface": "tpc-wg",
    "wg_source": "10.245.248.2",
    "control_interface": "tpc-p-ctl",
    "control_source": "10.245.249.10",
    "control_expected_source": "10.245.249.10",
    "dns_server": "1.1.1.1",
    "dns_name": "one.one.one.one",
    "https_address": "1.1.1.1",
    "https_name": "cloudflare-dns.com",
    "https_expected_source": str(https_ip),
    "vpn_udp_stun": True,
    "direct": {
        "address": "10.245.249.1",
        "port": 18080,
        "expected_source": "10.245.249.2",
    },
    "vpn": {
        "address": "162.159.207.0",
        "port": 3478,
        "expected_source": str(stun_ip),
    },
    "block": {
        "address": "10.245.249.1",
        "port": 18081,
    },
}

agent_env = (
    "TEMPEST_AGENT_ALLOWED_UIDS=10001\n"
    f"TEMPEST_AGENT_ALLOWED_GIDS={control_gid}\n"
    "TEMPEST_XRAY_LISTEN=10.245.249.2\n"
    "TEMPEST_XRAY_PORT=51820\n"
)

created = []

def write(path, content, gid, mode):
    with path.open("x") as f:
        created.append(path)
        os.fchown(f.fileno(), 0, gid)
        os.fchmod(f.fileno(), mode)
        f.write(content + "\n")

try:
    private.mkdir(mode=0o750)
    created.append(private)
    os.chown(private, 0, control_gid)
    private.chmod(0o750)

    write(private / "deployment.json",
          json.dumps(deployment, indent=2), control_gid, 0o640)

    write(private / "app-secret",
          secrets.token_urlsafe(48), control_gid, 0o640)

    write(private / "bootstrap-password",
          secrets.token_urlsafe(24), control_gid, 0o640)

    write(etc / "working-core-probe.key",
          probe_key, 0, 0o600)

    write(etc / "working-core-gateway.pub",
          public_key(gateway_key), 0, 0o644)

    write(etc / "working-core-probe.json",
          json.dumps(probe, indent=2), probe_gid, 0o640)

    write(etc / "xray-agent.env",
          agent_env.rstrip(), 0, 0o644)

except BaseException:
    for path in reversed(created):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    raise

print("TEMPEST credentials provisioned.")
