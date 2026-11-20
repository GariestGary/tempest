import asyncio
import base64
import json
import os
import socket
import subprocess
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.network_agent.models import IPCProtocolError
from app.network_agent.service import AgentServer, PeerPolicy
from app.network_agent.xray_agent import XrayAgent, XrayJournal
from app.network_agent.xray_client import XrayAgentClient
from app.network_agent.xray_profile import XrayPlan, parse_xray_request
from app.network_agent.xray_runtime import PROBE_UNIT, SystemdXray, XrayRuntimeError


def plan(generation=1, base=0, **changes):
    return XrayPlan.model_validate({
        "schema_version": 1, "target_namespace_id": "tpab-g",
        "generation_id": str(uuid4()), "generation": generation, "base_generation": base,
        "default_egress": "block", "listen_address": "192.0.2.1", "listen_port": 51871,
        "tunnel_addresses": ["10.88.0.1/32", "fd42:88::1/128"],
        "private_key": base64.b64encode(b"\x0a" * 32).decode(),
        "peers": [{
            "public_key": base64.b64encode(b"\x0b" * 32).decode(),
            "addresses": ["10.88.0.2/32", "fd42:88::2/128"],
        }],
        "vless": {"address": "192.0.2.20", "port": 8443, "client_id": str(uuid4()),
                  "server_name": "egress.poc.test", "certificate_sha256": "ab" * 32},
        "resolvers": ["198.51.100.53", "2001:db8:2::53"],
        "rules": [
            {"destination": "198.51.100.10/32", "port": 18080, "egress": "direct"},
            {"destination": "198.51.100.20/32", "port": 18080, "egress": "vpn"},
        ],
        **changes,
    })


def exchange(agent, method, config=None):
    request = {"protocol_version": 1, "request_id": str(uuid4()), "method": method}
    if method in {"validate", "stage", "diff"}:
        request["plan"] = config.model_dump(mode="json")
    elif method != "read_status":
        request["generation_id"] = str(config.generation_id)
    if method == "rollback":
        request["reason"] = "test"
    return agent.handle_request(parse_xray_request(json.dumps(request).encode()))


class Runtime:
    def __init__(self):
        self.active = None
        self.run = None
        self.starts = []
        self.bad = False
        self.failure = None

    def validate(self, payload):
        if self.bad:
            raise XrayRuntimeError("test checker rejected")

    def activate(self, payload):
        assert self.active is None
        self.active = payload
        self.run = str(uuid4()) if payload else None
        self.starts.append(payload)
        return self.run

    def assert_current(self, payload, run):
        if (payload, run) != (self.active, self.run):
            raise XrayRuntimeError("runtime drift")

    def verify(self, payload, run):
        self.assert_current(payload, run)
        if payload and payload == self.failure:
            raise XrayRuntimeError("test probe failed")

    def stop(self):
        self.active = self.run = None


@pytest.fixture
def state_dir(tmp_path):
    if os.name != "posix":
        pytest.skip("requires private POSIX journal directory locks")
    path = tmp_path / "state"
    path.mkdir(mode=0o700)
    return path


def make_agent(runtime, journal, clock=lambda: 0):
    return XrayAgent(runtime, journal, namespace="tpab-g", listener=("192.0.2.1", 51871), clock=clock)


def apply(agent, profile, *, confirm=True):
    assert exchange(agent, "stage", profile).ok
    assert exchange(agent, "apply", profile).ok
    if confirm:
        assert exchange(agent, "verify", profile).ok
        assert exchange(agent, "confirm", profile).ok


def test_profile_generates_narrow_default_deny():
    config = json.loads(plan().config_bytes())
    assert config["outbounds"][0]["protocol"] == "blackhole"
    assert config["routing"]["rules"][-1] == {
        "type": "field", "inboundTag": ["wg-mvp"], "outboundTag": "block",
    }
    assert [item["outboundTag"] for item in config["routing"]["rules"]] == [
        "vpn", "block", "direct", "vpn", "block",
    ]
    assert config["outbounds"][1]["settings"]["finalRules"][0]["ip"] == ["198.51.100.10/32"]
    stream = config["outbounds"][2]["streamSettings"]
    assert "sockopt" not in stream and stream["network"] == "raw"


@pytest.mark.parametrize("changes", [
    {"default_egress": "direct"}, {"outbounds": [{"protocol": "freedom"}]},
    {"rules": [{"destination": "::ffff:192.0.2.0/120", "port": 80, "egress": "direct"}]},
    {"listen_address": "0.0.0.0"}, {"rules": [{"destination": "0.0.0.0/0", "port": 80, "egress": "direct"}]},
    {"rules": [{"destination": "198.51.100.10/32", "port": 53, "egress": "direct"}]},
])
def test_semantics_reject_unsafe_profiles_before_xray(changes):
    with pytest.raises(ValidationError):
        plan(**changes)


def test_checker_before_replace_and_redacted_diff(state_dir):
    a, b = plan(), plan(2, 1)
    runtime = Runtime()
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        apply(agent, a)
        assert exchange(agent, "stage", b).ok
        assert runtime.active == a.config_bytes()
        diff = exchange(agent, "diff", b)
        assert b.private_key not in diff.model_dump_json()
        assert b.vless.client_id.hex not in diff.model_dump_json()
        runtime.bad = True
        assert not exchange(agent, "apply", b).ok
        assert runtime.active == a.config_bytes() and journal.read().lkg.plan == a


def test_verify_before_confirm_restart_and_timeout(state_dir):
    a, b, c = plan(), plan(2, 1), plan(3, 1)
    runtime = Runtime()
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        apply(agent, a)
        apply(agent, b, confirm=False)
        assert not exchange(agent, "confirm", b).ok
        assert journal.read().lkg.plan == a
    now = [0]
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal, clock=lambda: now[0])
        assert runtime.active == a.config_bytes()
        assert journal.read().pending is None
        assert not exchange(agent, "stage", b).ok
        apply(agent, c, confirm=False)
        now[0] = 91
        agent.maintain()
        assert runtime.active == a.config_bytes()
        assert not exchange(agent, "confirm", c).ok


@pytest.mark.parametrize("security", ["tls", "reality"])
def test_failed_readiness_rolls_back_only_to_safe_lkg(state_dir, security):
    a, b = plan(), plan(2, 1)
    if security == "reality":
        egress = b.vless.model_dump(mode="json", exclude={"certificate_sha256"})
        egress["reality"] = {
            "public_key": base64.urlsafe_b64encode(bytes(range(1, 33))).decode().rstrip("="),
            "short_id": "aabb", "flow": "xtls-rprx-vision",
        }
        b = plan(2, 1, vless=egress)
    runtime = Runtime()
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        apply(agent, a)
        runtime.failure = b.config_bytes()
        assert exchange(agent, "stage", b).ok
        assert not exchange(agent, "apply", b).ok
        assert runtime.active == a.config_bytes()
        runtime.failure = a.config_bytes()
        rebooted = make_agent(runtime, journal)
        assert exchange(rebooted, "read_status").result.active_generation == 0
        assert runtime.active is None


def test_revoke_prevents_rollback_restart_and_key_reuse(state_dir):
    a, b, c = plan(), plan(2, 1, peers=[]), plan(3, 0)
    runtime = Runtime()
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        apply(agent, a)
        apply(agent, b, confirm=False)
        assert runtime.active is None
        assert exchange(agent, "rollback", b).ok
        assert exchange(agent, "read_status").result.active_generation == 0
        assert not exchange(agent, "stage", c).ok
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        assert runtime.active is None
        assert not exchange(agent, "stage", c).ok
        assert runtime.starts.count(a.config_bytes()) == 1


def test_corrupted_durable_record_keeps_ingress_closed(state_dir):
    runtime = Runtime()
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        apply(agent, plan())
    (state_dir / "xray.json").write_text("{invalid")
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        assert runtime.active is None
        assert not exchange(agent, "stage", plan()).ok


def test_transport_rejects_raw_config_and_old_plan():
    with pytest.raises(IPCProtocolError):
        parse_xray_request(json.dumps({
            "protocol_version": 1, "request_id": str(uuid4()), "method": "stage",
            "plan": {"operations": [], "inbounds": []},
        }).encode())


def test_real_af_unix_authorized_apply(state_dir, tmp_path):
    if not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("requires Linux SO_PEERCRED")
    path = tmp_path / "control.sock"
    runtime = Runtime()
    with XrayJournal(state_dir) as journal, AgentServer(
        path, agent=make_agent(runtime, journal),
        peer_policy=PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()})),
        parse_request=parse_xray_request,
    ) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            async def scenario():
                a = plan()
                client = XrayAgentClient(str(path))
                stage = await client.request("stage", plan=a)
                assert stage.ok and runtime.active is None
                assert (await client.request("apply", generation_id=a.generation_id)).ok
                assert runtime.active == a.config_bytes()
            asyncio.run(scenario())
        finally:
            server.shutdown()
            worker.join()


def test_pinned_xray_accepts_generated_config(tmp_path):
    binary = os.environ.get("TEMPEST_TEST_XRAY")
    if not binary:
        pytest.skip("TEMPEST_TEST_XRAY not supplied")
    target = tmp_path / "candidate.json"
    tls = plan()
    variants = [("TLS", tls)]
    common = tls.vless.model_dump(mode="json", exclude={"certificate_sha256"})
    public_key = base64.urlsafe_b64encode(bytes(range(1, 33))).decode().rstrip("=")
    for fingerprint in ("chrome", "firefox", "safari", "ios", "edge"):
        for flow in ("", "xtls-rprx-vision"):
            egress = {**common, "reality": {
                "public_key": public_key, "fingerprint": fingerprint, "flow": flow,
                "short_id": "ab" if flow else "",
            }}
            variants.append((f"Reality/{fingerprint}/{flow or 'no flow'}", plan(vless=egress)))
    for name, profile in variants:
        target.write_bytes(profile.config_bytes())
        check = subprocess.run(
            [binary, "run", "-test", "-config", str(target)], capture_output=True, timeout=5,
        )
        assert check.returncode == 0, name


def test_confirm_rechecks_traffic_in_the_same_generation(state_dir):
    a, b = plan(), plan(2, 1)
    runtime = Runtime()
    with XrayJournal(state_dir) as journal:
        agent = make_agent(runtime, journal)
        apply(agent, a)
        apply(agent, b, confirm=False)
        assert exchange(agent, "verify", b).ok
        runtime.failure = b.config_bytes()
        assert not exchange(agent, "confirm", b).ok
        assert runtime.active == a.config_bytes()


def test_systemd_waits_for_previous_probe_and_retries_failed_live_traffic(monkeypatch):
    runtime = object.__new__(SystemdXray)
    calls = iter([("show", "activating"), ("show", "inactive"), ("start", None),
                  ("show", "failed"), ("start", "")])
    observed = []

    def systemctl(operation, unit, *args, **kwargs):
        expected, result = next(calls)
        assert operation == expected and unit == PROBE_UNIT
        observed.append(operation)
        if result is None:
            raise XrayRuntimeError("probe TCP timeout")
        return result

    monkeypatch.setattr(runtime, "_systemctl", systemctl)
    monkeypatch.setattr(runtime, "assert_current",
                        lambda payload, identity: observed.append((payload, identity)))
    monkeypatch.setattr("app.network_agent.xray_runtime.time.sleep", lambda seconds: None)
    runtime.verify(b"config", "run-id")
    assert observed.count((b"config", "run-id")) == 4
    assert observed.count("start") == 2
