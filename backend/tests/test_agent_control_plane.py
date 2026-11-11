"""Task 2.3 migration boundary: no legacy network path in Agent mode."""

import re
import os
import socket
import sys
import threading
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.network_agent.client import NetworkAgentClient, NetworkApplyUnsupported
from app.network_agent.mock import MockNetworkAgent
from app.network_agent.service import AgentServer, PeerPolicy
from tests.test_network_plan_contract import make_plan


def test_legacy_feature_flags_default_off():
    assert settings.legacy_router_enabled is False
    assert settings.legacy_wifi_dhcp_enabled is False
    assert settings.legacy_ssh_deploy_enabled is False
    assert settings.legacy_docker_sidecars_enabled is False


def test_agent_mode_blocks_every_unmigrated_api_and_websocket(client, monkeypatch):
    from app.core.dev_safe_mode import agent_mode_request_is_allowed
    from app.main import app
    from starlette.routing import WebSocketRoute
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    # Even explicit legacy flags must not turn an unmigrated API into a bypass.
    for flag in (
        "legacy_router_enabled", "legacy_wifi_dhcp_enabled",
        "legacy_ssh_deploy_enabled", "legacy_docker_sidecars_enabled",
    ):
        monkeypatch.setattr(settings, flag, True)

    blocked = 0
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path_template = getattr(route, "path", None)
        if not methods or not path_template:
            continue
        path = re.sub(r"\{[^}]+\}", "1", path_template)
        for method in methods:
            if agent_mode_request_is_allowed(method, path):
                continue
            response = client.request(method, path)
            assert response.status_code == 501, (method, path_template, response.text)
            assert response.json()["code"] == "NETWORK_PLAN_OPERATION_UNSUPPORTED"
            blocked += 1
    assert blocked >= 30

    for route in app.routes:
        if isinstance(route, WebSocketRoute):
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect(route.path):
                    pass
            assert closed.value.code == 1008


@pytest.mark.asyncio
async def test_agent_mode_lifespan_does_not_start_schedulers_or_host_operations(monkeypatch):
    from app.main import app, lifespan
    from app.core import router_mode, router_watchdog, xray, healthcheck, sub_scheduler
    from app.api import system

    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    with ExitStack() as stack:
        create_db = stack.enter_context(patch("app.main.create_db_and_tables"))
        init_db = stack.enter_context(patch("app.main.init_default_settings", new_callable=AsyncMock))
        watched = [
            stack.enter_context(patch.object(xray.xray_manager, "start", new_callable=AsyncMock)),
            stack.enter_context(patch.object(xray.xray_manager, "stop", new_callable=AsyncMock)),
            stack.enter_context(patch.object(healthcheck.health_checker, "start")),
            stack.enter_context(patch.object(sub_scheduler.subscription_scheduler, "start")),
            stack.enter_context(patch.object(router_mode, "reconcile_on_boot", new_callable=AsyncMock)),
            stack.enter_context(patch.object(router_watchdog.router_watchdog, "start")),
            stack.enter_context(patch.object(system, "apply_system_toggles_on_boot", new_callable=AsyncMock)),
        ]
        async with lifespan(app):
            pass
    create_db.assert_called_once_with()
    init_db.assert_awaited_once_with()
    for operation in watched:
        operation.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mode_direct_and_deferred_dataplane_fail_explicitly(monkeypatch):
    from app.core import dataplane
    from app.api import system
    from fastapi import BackgroundTasks

    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    tasks = BackgroundTasks()
    with patch("app.core.nftables.nftables_manager.apply_rules", new_callable=AsyncMock) as nft:
        with pytest.raises(NetworkApplyUnsupported):
            await dataplane.dispatch_dataplane(tasks, "start")
        with pytest.raises(NetworkApplyUnsupported):
            await dataplane.apply_dataplane("reload")
        with pytest.raises(NetworkApplyUnsupported):
            await system._apply_nftables(None, {"mode": "rules"})
    assert not tasks.tasks
    nft.assert_not_awaited()


@pytest.mark.asyncio
async def test_typed_client_refuses_mock_apply_and_missing_socket(tmp_path):
    plan = make_plan()
    client = NetworkAgentClient(str(tmp_path / "absent.sock"), timeout=0.1)
    with pytest.raises(NetworkApplyUnsupported):
        await client.apply(plan)
    from app.network_agent.client import NetworkAgentUnavailable
    with pytest.raises(NetworkAgentUnavailable):
        await client.validate(plan)


def test_plan_api_has_auth_and_fails_when_agent_absent(client, auth_headers, monkeypatch, tmp_path):
    from app.api import network_plan

    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    monkeypatch.setattr(network_plan.network_agent_client, "_socket_path", str(tmp_path / "absent.sock"))
    plan = make_plan().model_dump(mode="json")
    assert client.post("/api/network-plan/validate", json=plan).status_code == 401
    response = client.post("/api/network-plan/validate", json=plan, headers=auth_headers)
    assert response.status_code == 503
    assert client.post("/api/network-plan/validate", json={**plan, "command": "id"}, headers=auth_headers).status_code == 422


@pytest.mark.asyncio
@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"),
    reason="SO_PEERCRED transport requires Linux",
)
async def test_typed_client_uses_real_unix_transport_for_read_only_contract(tmp_path):
    path = tmp_path / "agent.sock"
    agent = MockNetworkAgent()
    server = AgentServer(
        path,
        agent=agent,
        peer_policy=PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()})),
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        client = NetworkAgentClient(str(path))
        plan = make_plan()
        assert (await client.validate(plan)).ok is True
        assert (await client.diff(plan)).ok is True
        assert (await client.read_status()).ok is True
        with pytest.raises(NetworkApplyUnsupported):
            await client.apply(plan)
        assert agent.read_status().active_generation == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
