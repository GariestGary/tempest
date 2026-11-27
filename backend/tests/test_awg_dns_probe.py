"""The lab verifier must reject truncated or missing DNSKEY answers."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


spec = importlib.util.spec_from_file_location(
    'working_core_awg_probe', Path(__file__).resolve().parents[2] / 'scripts/lab/working-core-awg-probe.py',
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_dnskey_verifier_rejects_truncation(monkeypatch):
    good = ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n;; flags: qr rd ra; QUERY: 1, ANSWER: 1\n. 10 IN DNSKEY 257 3 8 KEY\n;; MSG SIZE  rcvd: 1024\n'
    monkeypatch.setattr(probe, 'command', lambda *a, **k: SimpleNamespace(returncode=0, stdout=good))
    assert probe.dnskey_tcp()
    monkeypatch.setattr(probe, 'command', lambda *a, **k: SimpleNamespace(returncode=0, stdout=good.replace('qr rd', 'qr tc rd')))
    assert not probe.dnskey_tcp()
    monkeypatch.setattr(probe, 'command', lambda *a, **k: SimpleNamespace(returncode=0, stdout=good.replace('1024', '64')))
    assert not probe.dnskey_tcp()
    monkeypatch.setattr(probe, 'command', lambda *a, **k: None)
    assert not probe.dnskey_tcp()
