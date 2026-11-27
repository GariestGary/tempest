import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / 'deploy' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_socket_collisions_include_wildcards_and_ipv6():
    readiness = load('readiness', 'vortex-readiness.py')
    result = readiness.socket_conflicts('tcp LISTEN 0 4096 0.0.0.0:9080 0.0.0.0:*\nudp UNCONN 0 0 [::]:51820 [::]:*\ntcp LISTEN 0 9 127.0.0.1:5000 0.0.0.0:*')
    assert result['panel_tcp'] == ['0.0.0.0:9080']
    assert result['ingress_udp'] == ['[::]:51820']
    with pytest.raises(RuntimeError):
        readiness.socket_conflicts('unparseable')


def test_binary_verification_checks_digest_architecture_and_symlinks(tmp_path, monkeypatch):
    verify = load('verify_runtime', 'verify-runtime.py')
    header = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\x00'
    binary = tmp_path / 'binary'
    binary.write_bytes(header + b'test payload')
    monkeypatch.setitem(verify.EXPECTED, 'xray', hashlib.sha256(binary.read_bytes()).hexdigest())
    verify.verify('xray', binary)
    symlink = tmp_path / 'link'
    symlink.symlink_to(binary)
    with pytest.raises(OSError):
        verify.verify('xray', symlink)
    binary.write_bytes(header + b'changed')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        verify.verify('xray', binary)
    binary.write_bytes(b'not an executable')
    with pytest.raises(ValueError, match='not ELF64'):
        verify.verify('xray', binary)


def test_old_wireguard_preview_only_removes_reviewed_objects():
    import copy
    import json
    patch = load('old_wg_preview', 'preview-retire-old-wg.py')
    original = {'endpoints': [{'type': 'wireguard', 'tag': 'wg-clients', 'listen_port': 51820, 'private_key': 'private'}, {'type': 'wireguard', 'tag': 'other'}], 'route': {'rules': [{'inbound': ['other'], 'outbound': 'direct'}, {'inbound': ['wg-clients', 'other'], 'outbound': 'direct'}]}}
    payload = json.dumps(original).encode()
    digest = hashlib.sha256(payload).hexdigest()
    result = json.loads(patch.build(payload, digest))
    assert result['endpoints'] == original['endpoints'][1:]
    assert result['route']['rules'][1]['inbound'] == ['other']
    assert result['route']['rules'][0] == original['route']['rules'][0]
    bad = copy.deepcopy(original)
    bad['route']['rules'][1]['invert'] = True
    encoded = json.dumps(bad).encode()
    with pytest.raises(ValueError, match='unexpected old inbound rule'):
        patch.build(encoded, hashlib.sha256(encoded).hexdigest())
    bad = copy.deepcopy(original)
    bad['route']['final'] = 'wg-clients'
    encoded = json.dumps(bad).encode()
    with pytest.raises(ValueError, match='unexpected wg-clients reference'):
        patch.build(encoded, hashlib.sha256(encoded).hexdigest())
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        patch.build(payload, '0' * 64)


def test_production_probe_requires_distinct_control_path_and_all_checks(monkeypatch, capsys):
    probe = load('production_probe', 'working-core-probe.py')
    config = {
        'wg_interface': 'wg0', 'wg_source': '10.245.248.2',
        'control_interface': 'ctrl0', 'control_source': '10.244.246.2',
        'control_expected_source': '10.244.246.2', 'dns_server': '10.245.249.53',
        'dns_name': 'example.com', 'https_address': '1.1.1.1', 'https_name': 'cloudflare-dns.com',
        'direct': {'address': '10.244.240.10', 'port': 18080, 'expected_source': '10.244.246.2'},
        'vpn': {'address': '10.244.240.11', 'port': 18080, 'expected_source': '1.1.1.2'},
        'block': {'address': '10.244.240.12', 'port': 18080},
    }
    parsed = probe.parse_config(config)
    same_path = dict(config, control_interface='wg0')
    with pytest.raises(ValueError, match='interfaces must differ'):
        probe.parse_config(same_path)
    monkeypatch.setattr(probe, 'dns', lambda *args: True)
    monkeypatch.setattr(probe, 'https', lambda *args: True)
    monkeypatch.setattr(probe, 'echo', lambda target, source, interface, protocol, expected, blocked=False: True)
    assert probe.run(parsed)
    monkeypatch.setattr(probe, 'echo', lambda target, source, interface, protocol, expected, blocked=False: interface != 'ctrl0')
    assert not probe.run(parsed)
    assert 'independent BLOCK control unavailable' in capsys.readouterr().out
    monkeypatch.setattr(probe, 'echo', lambda target, source, interface, protocol, expected, blocked=False: not blocked)
    assert not probe.run(parsed)


def test_production_probe_rejects_route_errors_and_ambiguous_block_failures(monkeypatch):
    probe = load('probe_failure_modes', 'working-core-probe.py')
    target = {'address': '10.244.240.12', 'port': 18080}
    monkeypatch.setattr(probe, 'route_is', lambda *args: False)
    assert not probe.echo(target, '10.245.248.2', 'wg0', 'tcp', '', blocked=True)
    monkeypatch.setattr(probe, 'route_is', lambda *args: True)
    class Refused:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def settimeout(self, value): pass
        def bind(self, value): pass
        def connect(self, value): raise ConnectionRefusedError()
    monkeypatch.setattr(probe.socket, 'socket', lambda *args: Refused())
    assert not probe.echo(target, '10.245.248.2', 'wg0', 'tcp', '', blocked=True)


def test_production_probe_requires_complete_dnskey(monkeypatch):
    from types import SimpleNamespace
    probe = load('probe_dnskey', 'working-core-probe.py')
    monkeypatch.setattr(probe, 'route_is', lambda *args: True)
    config = {'dns_server': '10.245.249.53', 'wg_source': '10.245.248.2', 'wg_interface': 'wg0'}
    good = 'status: NOERROR\nflags: qr rd ra;\n. IN DNSKEY 257 3 8 key\nMSG SIZE  rcvd: 900\n'
    monkeypatch.setattr(probe.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=good))
    assert probe.dns(config, '+tcp', True)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=good.replace('qr rd ra', 'qr tc rd ra')))
    assert not probe.dns(config, '+tcp', True)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=good.replace('900', '100')))
    assert not probe.dns(config, '+tcp', True)


def test_probe_fixture_replies_only_to_random_markers_and_allowlisted_source():
    import ipaddress
    fixture = load('production_echo', 'working-core-echo.py')
    marker = b'TEMPEST-' + b'a' * 32 + b'\n'
    allowed = {ipaddress.ip_address('10.244.240.10')}
    assert fixture.response(marker, '10.244.240.10', allowed) == marker + b'10.244.240.10'
    assert fixture.response(marker, '10.244.240.11', allowed) == b''
    assert fixture.response(b'not-a-probe\n', '10.244.240.10', allowed) == b''
    assert fixture.response(marker, 'bad-address', allowed) == b''


def test_probe_echo_fixture_real_loopback_tcp_and_udp(monkeypatch):
    import select
    import socket
    import subprocess
    import sys

    probe = load('probe_loopback', 'working-core-probe.py')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    fixture = subprocess.Popen([sys.executable, str(ROOT / 'scripts/deploy/working-core-echo.py'),
                                '--listen', '127.0.0.1', '--port', str(port), '--allow', '127.0.0.1'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([fixture.stdout], [], [], 3)[0], 'fixture did not become ready'
        assert fixture.stdout.readline().strip() == 'fixture ready'
        monkeypatch.setattr(probe, 'route_is', lambda *args: True)
        target = {'address': '127.0.0.1', 'port': port}
        assert probe.echo(target, '127.0.0.1', 'lo', 'tcp', '127.0.0.1')
        assert probe.echo(target, '127.0.0.1', 'lo', 'udp', '127.0.0.1')
    finally:
        fixture.terminate()
        fixture.communicate(timeout=3)
