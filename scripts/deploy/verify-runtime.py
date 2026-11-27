#!/usr/bin/env python3
"""Verify operator-supplied Linux amd64 artifacts without executing or installing them."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys

EXPECTED = {
    'xray': 'cc4b84d3069569f297a61f04b914f2bb37e9d202fd7b84cb50def1fc30fd40e1',
    'awgproxy': 'e0fe256ad362221026a15a372a246b21026b0405e4dc32ab8f2c87c92543686e',
    'sing-box': 'fc9c6e6ab345f045b16a0ed10d1ff28d68e8e56e7749fca30738d1406e98d7b8',
}


def verify(name, path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError('not a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            header = stream.read(20)
            if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\x3e\x00':
                raise ValueError('not ELF64 little-endian amd64')
            stream.seek(0)
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != EXPECTED[name]:
            raise ValueError('SHA-256 mismatch')
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in EXPECTED:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    failed = False
    for name in EXPECTED:
        try:
            verify(name, getattr(args, name.replace('-', '_')))
            print(f'{name}: PASS (linux/amd64 and pinned SHA-256)')
        except (OSError, ValueError) as exc:
            print(f'{name}: FAIL ({exc})', file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
