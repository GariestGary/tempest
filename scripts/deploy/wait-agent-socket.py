#!/usr/bin/env python3
"""Wait for a live Agent service and connectable Unix socket; do not send IPC."""
import argparse
import socket
import subprocess
import time
from pathlib import Path


def wait_for_agent(path: Path, timeout: float, active) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if active() and path.is_socket():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(0.5)
                    client.connect(str(path))
                return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, default=Path('/run/tempest-xray-agent/control.sock'))
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    if not 0 < args.timeout <= 120 or not args.socket.is_absolute():
        parser.error('absolute socket path and timeout in (0, 120] required')
    active = lambda: subprocess.run(['systemctl', 'is-active', '--quiet', 'tempest-xray-agent.service'], check=False).returncode == 0
    if not wait_for_agent(args.socket, args.timeout, active):
        parser.exit(1, 'Agent service or socket not ready\n')
    print('Agent service active and Unix socket connectable; IPC authorization and health not verified.')


if __name__ == '__main__':
    main()
