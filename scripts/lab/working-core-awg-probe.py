#!/usr/bin/env python3
"""Verify the approved tpab fixture; never alter network state or print traffic."""
import ipaddress
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor


def command(*args, timeout=4):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def dns(transport):
    result = command('dig', '-r', '@198.51.100.53', 'one.one.one.one', 'A',
                     '+short', '+timeout=2', '+tries=1', transport)
    if result is None or result.returncode:
        return False
    try:
        return any(ipaddress.ip_address(value).version == 4 for value in result.stdout.split())
    except ValueError:
        return False


def vpn_https():
    result = command('curl', '-fsS', '--noproxy', '*', '--connect-timeout', '2', '--max-time', '3',
                     '--resolve', 'cloudflare-dns.com:443:1.1.1.1',
                     'https://cloudflare-dns.com/cdn-cgi/trace')
    if result is None or result.returncode:
        return False
    match = re.search(r'^ip=([^\s]+)$', result.stdout, re.MULTILINE)
    try:
        return bool(match and ipaddress.ip_address(match.group(1)).is_global)
    except ValueError:
        return False


def legacy_or_awg():
    active = command('systemctl', 'is-active', '--quiet', 'tempest-awgproxy.service', timeout=2)
    if active is None:
        return False
    if active.returncode:
        result = command('/opt/tempest/bin/xray-mvp-probe', timeout=5)
        return result is not None and result.returncode == 0
    # Independently prove the explicit DIRECT and BLOCK rules via the same
    # controlled fixture; imported profiles and public IPs never enter logs.
    from xray_mvp_probe_checks import fixture_controls
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = [pool.submit(dns, '+tcp'), pool.submit(dns, '+notcp'),
                  pool.submit(vpn_https), pool.submit(fixture_controls)]
        results = [check.result() for check in checks]
        print('AWG checks (DNS TCP, DNS UDP, HTTPS, DIRECT/BLOCK):',
              *('pass' if result else 'fail' for result in results), flush=True)
        return all(results)


if __name__ == '__main__':
    ok = legacy_or_awg()
    print('AWG-aware traffic probe ' + ('passed' if ok else 'failed'), flush=True)
    raise SystemExit(0 if ok else 1)
