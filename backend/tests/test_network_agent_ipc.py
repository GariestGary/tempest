"""Linux AF_UNIX integration checks for the privilege-free Network Agent."""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

from app.network_agent.mock import MockNetworkAgent
from app.network_agent.models import MAX_IPC_MESSAGE_BYTES
from app.network_agent.service import (
    AgentServer,
    PeerIdentity,
    PeerPolicy,
    ReplayConflict,
    ReplayGuard,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"),
    reason="AF_UNIX SO_PEERCRED integration is Linux-only",
)


def _exchange(path: Path, payload: bytes, *, timeout: float = 1) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        client.sendall(struct.pack("!I", len(payload)) + payload)
        header = _read_exact(client, 4)
        size = struct.unpack("!I", header)[0]
        return _read_exact(client, size)


def _read_exact(connection: socket.socket, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        chunk = connection.recv(size - len(output))
        if not chunk:
            raise EOFError("agent closed connection")
        output.extend(chunk)
    return bytes(output)


@pytest.fixture
def running_agent(tmp_path: Path):
    agent = MockNetworkAgent()
    path = tmp_path / "agent.sock"
    server = AgentServer(
        path,
        agent=agent,
        peer_policy=PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()})),
        io_timeout_seconds=0.2,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield agent, path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(request_id: int, method: str = "read_status") -> bytes:
    return json.dumps(
        {
            "protocol_version": 1,
            "request_id": str(UUID(int=request_id)),
            "method": method,
        }
    ).encode()


def test_allowed_peer_can_connect_and_audit_uses_kernel_identity(running_agent, caplog) -> None:
    caplog.set_level(logging.INFO, logger="tempest.network_agent")
    agent, path = running_agent
    response = json.loads(_exchange(path, _request(1)))
    assert response["ok"] is True
    assert agent.audit_log[-1]["peer_uid"] is None  # Mock never trusts JSON identity.
    assert f"uid={os.getuid()} gid={os.getgid()} pid=" in caplog.text


def test_peer_policy_rejects_unapproved_uid_or_gid() -> None:
    policy = PeerPolicy(frozenset({1001}), frozenset({2001}))
    with pytest.raises(PermissionError):
        policy.authorize(PeerIdentity(pid=55, uid=1002, gid=2001))
    with pytest.raises(PermissionError):
        policy.authorize(PeerIdentity(pid=55, uid=1001, gid=2002))


def test_server_closes_connection_for_unapproved_peer(tmp_path: Path) -> None:
    path = tmp_path / "denied.sock"
    server = AgentServer(
        path,
        agent=MockNetworkAgent(),
        peer_policy=PeerPolicy(frozenset({os.getuid() + 1}), frozenset({os.getgid()})),
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(path))
            client.sendall(struct.pack("!I", len(_request(20))) + _request(20))
            try:
                result = client.recv(1)
            except ConnectionResetError:
                result = b""
            assert result == b""
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_spoofed_identity_fields_are_rejected(running_agent) -> None:
    agent, path = running_agent
    forged = json.dumps(
        {
            "protocol_version": 1,
            "request_id": str(UUID(int=2)),
            "method": "read_status",
            "uid": os.getuid(),
            "gid": os.getgid(),
            "pid": os.getpid(),
            "actor": "trusted-control-plane",
        }
    ).encode()
    with pytest.raises((EOFError, ConnectionError, OSError)):
        _exchange(path, forged)
    assert agent.audit_log == []


@pytest.mark.parametrize(
    "payload",
    [b"{", _request(3, "exec"), _request(4)[:-1] + b',"extra":true}'],
)
def test_malformed_unknown_or_extra_field_request_is_rejected(running_agent, payload: bytes) -> None:
    agent, path = running_agent
    with pytest.raises((EOFError, ConnectionError, OSError)):
        _exchange(path, payload)
    assert agent.audit_log == []


def test_oversized_message_is_rejected_before_body_read(running_agent) -> None:
    agent, path = running_agent
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(path))
        client.sendall(struct.pack("!I", MAX_IPC_MESSAGE_BYTES + 1))
        client.settimeout(1)
        assert client.recv(1) == b""
    assert agent.audit_log == []


def test_identical_request_retry_returns_cached_response_without_dispatch(running_agent) -> None:
    agent, path = running_agent
    payload = _request(5)
    first = _exchange(path, payload)
    audit_count = len(agent.audit_log)
    second = _exchange(path, payload)
    assert first == second
    assert len(agent.audit_log) == audit_count


def test_lost_response_retry_does_not_dispatch_twice(running_agent) -> None:
    agent, path = running_agent
    payload = _request(21)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(path))
        client.sendall(struct.pack("!I", len(payload)) + payload)
    deadline = time.monotonic() + 1
    while not agent.audit_log and time.monotonic() < deadline:
        time.sleep(0.005)
    count_after_first_dispatch = len(agent.audit_log)
    assert count_after_first_dispatch == 1
    retried = json.loads(_exchange(path, payload))
    assert retried["ok"] is True
    assert len(agent.audit_log) == count_after_first_dispatch


def test_request_id_reuse_with_different_payload_is_rejected(running_agent) -> None:
    agent, path = running_agent
    _exchange(path, _request(6))
    with pytest.raises((EOFError, ConnectionError, OSError)):
        _exchange(path, _request(6, "read_status")[:-1] + b" ")
    assert len(agent.audit_log) == 1


def test_incomplete_frame_timeout_leaves_agent_state_unchanged(running_agent) -> None:
    agent, path = running_agent
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(path))
        client.sendall(struct.pack("!I", 20) + b"{}")
        time.sleep(0.3)
        assert client.recv(1) == b""
    assert agent.read_status().active_generation == 0
    assert agent.audit_log == []


def test_concurrent_clients_are_served_without_state_races(running_agent) -> None:
    agent, path = running_agent
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda request_id: _exchange(path, _request(request_id)), range(30, 38)))
    assert all(json.loads(response)["ok"] for response in responses)
    assert len(agent.audit_log) == 8


def test_systemd_unit_declares_empty_capabilities_and_restrictive_sandbox() -> None:
    unit = Path(__file__).parents[2] / "deploy" / "systemd" / "tempest-network-agent.service"
    content = unit.read_text(encoding="utf-8")
    for directive in (
        "User=tempest-agent",
        "Group=tempest-control",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "InaccessiblePaths=-/etc/NetworkManager -/etc/systemd -/etc/ssh -/var/lib/docker -/run/docker.sock -/var/run/docker.sock",
        "RestrictAddressFamilies=AF_UNIX",
        "ReadWritePaths=/run/tempest-agent",
    ):
        assert directive in content
    assert "CAP_NET_ADMIN" not in content


def test_standalone_module_starts_without_fastapi_process(tmp_path: Path) -> None:
    path = tmp_path / "standalone.sock"
    backend = Path(__file__).parents[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.network_agent",
            f"--socket={path}",
            f"--allowed-uids={os.getuid()}",
            f"--allowed-gids={os.getgid()}",
        ],
        cwd=backend,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3
        while not path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process.poll() is None
        assert json.loads(_exchange(path, _request(40)))["ok"] is True
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_replay_guard_fails_closed_when_bounded_cache_is_full() -> None:
    guard = ReplayGuard(max_entries=1)
    first_id = UUID(int=10)
    second_id = UUID(int=11)
    calls = 0

    def callback() -> bytes:
        nonlocal calls
        calls += 1
        return b"response"

    assert guard.execute(first_id, b"one", callback) == b"response"
    assert guard.execute(first_id, b"one", callback) == b"response"
    with pytest.raises(ReplayConflict, match="cache is full"):
        guard.execute(second_id, b"two", callback)
    assert guard.execute(first_id, b"one", callback) == b"response"
    assert calls == 1
