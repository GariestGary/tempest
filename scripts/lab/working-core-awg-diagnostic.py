#!/usr/bin/env python3
"""Classify AWG startup failure without logging config or upstream output."""
import subprocess
import re

try:
    result = subprocess.run(
        ['/opt/tempest/bin/awgproxy', '-s', '-c', '/run/tempest-xray/diagnostic-awg.conf'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6,
    )
except subprocess.TimeoutExpired:
    print('AWG diagnostic: process stayed running', flush=True)
    raise SystemExit(0)

output = (result.stdout + result.stderr).lower()
checks = {
    'permission': (b'permission denied', b'operation not permitted'),
    'address_collision': (b'address already in use',),
    'address_family': (b'address family not supported by protocol',),
    'filesystem': (b'no such file or directory', b'file does not exist'),
    'landlock': (b'landlock',),
    'device_configuration': (b'ipc', b'configuration', b'bad value', b'invalid argument'),
    'memory': (b'cannot allocate memory', b'out of memory'),
}
categories = [name for name, patterns in checks.items() if any(pattern in output for pattern in patterns)]
safe_words = frozenset('error failed failure could unable cannot create start load open read write setup apply device config configuration wireguard awg netstack bind socket address port runtime landlock no such file directory set protect supported unsupported network operation key invalid range peer no type flags ruleset size field value denied bad name format syntax unmarshal empty exists handle configtest sandbox unexpected end user timeout stopped'.split())
words = sorted({word for word in re.findall(r'[a-z]+', output.decode('utf-8', errors='replace')) if word in safe_words})
print('AWG diagnostic: exited', result.returncode, 'categories:', ','.join(categories) or 'other',
      'safe words:', ','.join(words), flush=True)
raise SystemExit(1)
