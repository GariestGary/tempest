#!/usr/bin/env python3
"""Temporary single-endpoint UDP bridge for the isolated tpab lab only.

The root supervisor owns two exact namespace-local objects. The two data-plane
workers run without capabilities as the existing Xray UID; no host route,
resolver or firewall is modified. The endpoint file contains no keys.
"""
import ipaddress
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time

RUN = Path('/run/tempest-awg-lab-relay')
ENDPOINT = Path('/var/lib/tempest-awg-trial/relay-endpoint')
UUID = 'ce5fdfe6-0c5d-4487-82d8-f158cdb89609'


def run(*args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def endpoint(path):
    host, port = path.read_text().strip().rsplit(':', 1)
    ip = ipaddress.ip_address(host)
    if ip.version != 4 or not ip.is_global or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('lab relay requires a global IPv4 endpoint')
    return str(ip), int(port)


def worker(mode):
    host, port = endpoint(RUN / 'endpoint')
    own = RUN / (mode + '.sock')
    other = RUN / ('guest.sock' if mode == 'host' else 'host.sock')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as bridge, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        bridge.bind(str(own))
        os.chmod(own, 0o600)
        if mode == 'host':
            udp.connect((host, port))
        else:
            udp.bind((host, port))
        sel = selectors.DefaultSelector()
        sel.register(bridge, selectors.EVENT_READ, 'bridge')
        sel.register(udp, selectors.EVENT_READ, 'udp')
        peer = None
        while True:
            for key, _ in sel.select():
                if key.data == 'bridge':
                    data, sender = bridge.recvfrom(65535)
                    if sender != str(other):
                        continue
                    if mode == 'host':
                        udp.send(data)
                    elif peer is not None:
                        udp.sendto(data, peer)
                else:
                    data, sender = udp.recvfrom(65535)
                    if mode == 'guest':
                        if sender[0] != '203.0.113.1':
                            continue
                        peer = sender
                    try:
                        bridge.sendto(data, str(other))
                    except (FileNotFoundError, ConnectionRefusedError):
                        pass


def supervise():
    if os.geteuid() != 0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip() != UUID:
        raise SystemExit('Wrong VM or UID')
    host, _ = endpoint(ENDPOINT)
    # systemd runs this supervisor in the VM host namespace. PID 1 was also
    # compared separately during preflight; avoid granting CAP_SYS_PTRACE just
    # so this service can inspect a different UID's procfs namespace link.
    supervisor_ns = os.stat('/proc/self/ns/net')
    if any(os.path.samestat(supervisor_ns, os.stat('/run/netns/' + ns)) for ns in ('tpab-g', 'tpab-s')):
        raise SystemExit('Supervisor namespace collision')
    if RUN.exists():
        raise SystemExit('Relay runtime path already exists')
    if subprocess.check_output(['ip', '-n', 'tpab-g', 'route', 'show', 'exact', host + '/32'], text=True).strip():
        raise SystemExit('Endpoint route already exists')
    import json
    addresses = json.loads(subprocess.check_output(['ip', '-n', 'tpab-s', '-j', 'addr', 'show'], text=True))
    if any(info.get('local') == host for dev in addresses for info in dev.get('addr_info', [])):
        raise SystemExit('Endpoint address already exists')
    uid = int(subprocess.check_output(['id', '-u', 'tempest-xray'], text=True))
    gid = int(subprocess.check_output(['id', '-g', 'tempest-xray'], text=True))
    # Use the existing service group for the transient directory; no CAP_CHOWN
    # or extra privileges are needed by the supervisor.
    os.setegid(gid)
    try:
        RUN.mkdir(mode=0o770)
        os.chmod(RUN, 0o770)
        config = RUN / 'endpoint'
        config.write_text(ENDPOINT.read_text())
        os.chmod(config, 0o640)
    except BaseException:
        shutil.rmtree(RUN, ignore_errors=True)
        raise
    finally:
        os.setegid(0)
    address_added = route_added = False
    children = []
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        run('ip', '-n', 'tpab-s', 'addr', 'add', host + '/32', 'dev', 'lo')
        address_added = True
        run('ip', '-n', 'tpab-g', 'route', 'add', host + '/32', 'via', '203.0.113.2', 'dev', 'tpab-gs')
        route_added = True
        identity = ['setpriv', '--reuid=' + str(uid), '--regid=' + str(gid), '--clear-groups']
        executable = str(Path(__file__).resolve())
        children.append(subprocess.Popen([*identity, '/usr/bin/python3', executable, 'host']))
        children.append(subprocess.Popen(['ip', 'netns', 'exec', 'tpab-s', *identity, '/usr/bin/python3', executable, 'guest']))
        for _ in range(50):
            if (RUN / 'host.sock').exists() and (RUN / 'guest.sock').exists():
                break
            if any(child.poll() is not None for child in children):
                raise RuntimeError('relay worker failed')
            time.sleep(0.1)
        else:
            raise RuntimeError('relay worker readiness timeout')
        notify = os.environ.get('NOTIFY_SOCKET')
        if notify:
            address = '\0' + notify[1:] if notify.startswith('@') else notify
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
                notifier.sendto(b'READY=1', address)
        print('Isolated AWG relay ready', flush=True)
        while not stopped:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('relay worker exited')
            time.sleep(0.2)
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if route_added:
            run('ip', '-n', 'tpab-g', 'route', 'del', host + '/32', 'via', '203.0.113.2', 'dev', 'tpab-gs', check=False)
        if address_added:
            run('ip', '-n', 'tpab-s', 'addr', 'del', host + '/32', 'dev', 'lo', check=False)
        shutil.rmtree(RUN)


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] in {'host', 'guest'}:
        worker(sys.argv[1])
    elif len(sys.argv) == 1:
        supervise()
    else:
        raise SystemExit('invalid relay mode')
