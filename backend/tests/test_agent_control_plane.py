"""Task 2.3 migration boundary: no legacy network path in Agent mode."""

import re
import os
import socket
import sys
import threading
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.config import settings
from app.network_agent.client import NetworkAgentClient, NetworkApplyUnsupported
from app.network_agent.mock import MockNetworkAgent
from app.network_agent.models import AgentStatus, IPCResponse, NetworkDiff, ValidationResult
from app.network_agent.service import AgentServer, PeerPolicy
from tests.test_network_plan_contract import make_plan


@pytest.fixture
def agent_mode(monkeypatch):
    """Set the safe profile before TestClient enters the application lifespan."""
    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")


@pytest.fixture
def agent_client(agent_mode, client):
    return client


def test_legacy_feature_flags_default_off():
    assert settings.legacy_router_enabled is False
    assert settings.legacy_wifi_dhcp_enabled is False
    assert settings.legacy_ssh_deploy_enabled is False
    assert settings.legacy_docker_sidecars_enabled is False


def test_agent_mode_blocks_every_unmigrated_api_and_websocket(agent_client, monkeypatch):
    from app.core.dev_safe_mode import agent_mode_request_is_allowed
    from app.main import app
    from starlette.routing import WebSocketRoute
    from starlette.websockets import WebSocketDisconnect

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
            response = agent_client.request(method, path)
            assert response.status_code == 501, (method, path_template, response.text)
            if method != "HEAD":
                assert response.json()["code"] == "NETWORK_PLAN_OPERATION_UNSUPPORTED"
            blocked += 1
    assert blocked >= 30

    for route in app.routes:
        if isinstance(route, WebSocketRoute):
            with pytest.raises(WebSocketDisconnect) as closed:
                with agent_client.websocket_connect(route.path):
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
async def test_agent_mode_internal_network_services_fail_before_side_effects(monkeypatch):
    """Calling legacy internals directly must not bypass HTTP middleware."""
    from app.core import network_apply, network_config, nftables, router_mode, tun

    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    with ExitStack() as stack:
        subprocess_run = stack.enter_context(patch("app.core.network_config.subprocess.run"))
        process_start = stack.enter_context(patch("asyncio.create_subprocess_exec", new_callable=AsyncMock))
        file_open = stack.enter_context(patch("builtins.open"))

        with pytest.raises(NetworkApplyUnsupported):
            network_config.host_run(["ip", "route", "replace", "default"])
        with pytest.raises(NetworkApplyUnsupported):
            network_apply.apply(None)
        with pytest.raises(NetworkApplyUnsupported):
            network_apply.rollback()
        with pytest.raises(NetworkApplyUnsupported):
            router_mode.set_ip_forward(True)
        with pytest.raises(NetworkApplyUnsupported):
            await nftables._nft("flush ruleset")
        with pytest.raises(NetworkApplyUnsupported):
            await nftables._run_exec("ip", "route", "replace", "default")
        with pytest.raises(NetworkApplyUnsupported):
            await tun.setup_tun()
        with pytest.raises(NetworkApplyUnsupported):
            await tun.teardown_tun()

    subprocess_run.assert_not_called()
    process_start.assert_not_awaited()
    file_open.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mode_internal_engine_and_legacy_service_calls_fail_closed(monkeypatch):
    from app.api import system
    from app.core import circle_scheduler, config_gen, dhcp, healthcheck, naive_manager, ssh, updater, wifi, xray

    monkeypatch.setattr(settings, "dev_safe_mode", False)
    monkeypatch.setattr(settings, "control_plane_mode", "agent")
    with ExitStack() as stack:
        process_start = stack.enter_context(patch("asyncio.create_subprocess_exec", new_callable=AsyncMock))
        docker_start = stack.enter_context(patch.object(wifi, "_docker_client"))
        dhcp_docker_start = stack.enter_context(patch.object(dhcp, "_docker_client"))
        naive_write = stack.enter_context(patch.object(naive_manager.naive_manager, "_write_config_sync"))
        engine_lock = stack.enter_context(patch.object(xray.xray_manager, "_acquire_lock_or_raise", new_callable=AsyncMock))
        host_open = stack.enter_context(patch("builtins.open"))
        update_dir = stack.enter_context(patch.object(type(updater.DATA_DIR), "mkdir"))

        with pytest.raises(NetworkApplyUnsupported):
            await xray.xray_manager.start()
        with pytest.raises(NetworkApplyUnsupported):
            await xray.xray_manager.stop()
        with pytest.raises(NetworkApplyUnsupported):
            await xray.xray_manager._start_unlocked()
        with pytest.raises(NetworkApplyUnsupported):
            await config_gen.write_config({}, validate=False)
        with pytest.raises(NetworkApplyUnsupported):
            wifi._start_sync(None)
        with pytest.raises(NetworkApplyUnsupported):
            dhcp._start_sync(None)
        with pytest.raises(NetworkApplyUnsupported):
            await naive_manager.naive_manager.start_node(None)
        with pytest.raises(NetworkApplyUnsupported):
            await ssh.exec_remote_script(host="example.invalid", script_content="true")
        with pytest.raises(NetworkApplyUnsupported):
            await system.apply_system_toggles_on_boot()
        with pytest.raises(NetworkApplyUnsupported):
            updater.write_request(version="1.0")
        checker = healthcheck.HealthChecker()
        with pytest.raises(NetworkApplyUnsupported):
            checker.start()
        assert checker._task is None
        with pytest.raises(NetworkApplyUnsupported):
            await circle_scheduler.circle_scheduler.rotate_circle(1)

    process_start.assert_not_awaited()
    docker_start.assert_not_called()
    dhcp_docker_start.assert_not_called()
    naive_write.assert_not_awaited()
    engine_lock.assert_not_awaited()
    host_open.assert_not_called()
    update_dir.assert_not_called()


def test_agent_boundary_preserves_legacy_profile(monkeypatch):
    from app.core.agent_boundary import require_legacy_engine, require_legacy_network

    monkeypatch.setattr(settings, "control_plane_mode", "legacy")
    require_legacy_network("legacy test operation")
    require_legacy_engine("legacy test engine operation")


@pytest.mark.asyncio
async def test_typed_client_refuses_mock_apply_and_missing_socket(tmp_path):
    plan = make_plan()
    client = NetworkAgentClient(str(tmp_path / "absent.sock"), timeout=0.1)
    with pytest.raises(NetworkApplyUnsupported):
        await client.apply(plan)
    from app.network_agent.client import NetworkAgentUnavailable
    with pytest.raises(NetworkAgentUnavailable):
        await client.validate(plan)


def test_plan_api_has_auth_and_fails_when_agent_absent(agent_client, auth_headers, monkeypatch, tmp_path):
    from app.api import network_plan

    monkeypatch.setattr(network_plan.network_agent_client, "_socket_path", str(tmp_path / "absent.sock"))
    plan = make_plan().model_dump(mode="json")
    assert agent_client.post("/api/network-plan/validate", json=plan).status_code == 401
    response = agent_client.post("/api/network-plan/validate", json=plan, headers=auth_headers)
    assert response.status_code == 503
    assert agent_client.post("/api/network-plan/validate", json={**plan, "command": "id"}, headers=auth_headers).status_code == 422


def test_current_plan_is_db_derived_and_agent_validated(agent_client, auth_headers, sample_node, monkeypatch):
    from app.api import network_plan

    status = IPCResponse(
        request_id=uuid4(), ok=True,
        result=AgentStatus(state="idle", active_generation=0, last_known_good_generation=0),
    )
    monkeypatch.setattr(network_plan.network_agent_client, "read_status", AsyncMock(return_value=status))
    monkeypatch.setattr(
        network_plan.network_agent_client,
        "validate",
        AsyncMock(side_effect=lambda plan: IPCResponse(
            request_id=uuid4(), ok=True,
            result=ValidationResult(
                generation_id=plan.generation_id,
                fingerprint=plan.fingerprint(),
                operation_count=len(plan.operations),
                permitted_egress_count=len(plan.egress_candidates),
            ),
        )),
    )
    monkeypatch.setattr(
        network_plan.network_agent_client,
        "diff",
        AsyncMock(side_effect=lambda plan: IPCResponse(
            request_id=uuid4(), ok=True,
            result=NetworkDiff(
                generation_id=plan.generation_id,
                base_generation=plan.base_generation,
                target_generation=plan.generation,
            ),
        )),
    )

    current = agent_client.get("/api/network-plan/current", headers=auth_headers)
    assert current.status_code == 200
    assert current.json()["default_egress_id"] == f"tempest-egress-node-{sample_node.id}"
    assert current.json()["operations"][0]["verdict"] == "block"
    assert agent_client.post("/api/network-plan/current/validate", headers=auth_headers).status_code == 200
    assert agent_client.post("/api/network-plan/current/diff", headers=auth_headers).status_code == 200
    assert network_plan.network_agent_client.validate.await_count == 1
    assert network_plan.network_agent_client.diff.await_count == 1


def test_current_plan_fails_closed_for_empty_db_configuration(agent_client, auth_headers, monkeypatch):
    from app.api import network_plan

    monkeypatch.setattr(
        network_plan.network_agent_client,
        "read_status",
        AsyncMock(return_value=IPCResponse(
            request_id=uuid4(), ok=True,
            result=AgentStatus(state="idle", active_generation=0, last_known_good_generation=0),
        )),
    )
    validate = AsyncMock()
    monkeypatch.setattr(network_plan.network_agent_client, "validate", validate)

    response = agent_client.post("/api/network-plan/current/validate", headers=auth_headers)
    assert response.status_code == 422
    assert "no enabled egress" in response.json()["detail"]
    validate.assert_not_awaited()


def test_current_plan_fails_closed_when_agent_is_absent(agent_client, auth_headers, sample_node, monkeypatch, tmp_path):
    from app.api import network_plan

    monkeypatch.setattr(
        network_plan.network_agent_client,
        "_socket_path",
        str(tmp_path / "agent-not-installed.sock"),
    )
    response = agent_client.get("/api/network-plan/current", headers=auth_headers)
    assert response.status_code == 503


def test_plan_api_maps_agent_conflict_to_http_conflict(agent_client, auth_headers, monkeypatch):
    from app.api import network_plan
    from app.network_agent.client import NetworkAgentRejected

    monkeypatch.setattr(
        network_plan.network_agent_client,
        "validate",
        AsyncMock(side_effect=NetworkAgentRejected("conflict", "validate")),
    )
    response = agent_client.post(
        "/api/network-plan/validate", json=make_plan().model_dump(mode="json"), headers=auth_headers
    )
    assert response.status_code == 409


def test_concurrent_db_plan_inspections_are_read_only(agent_mode, engine, sample_node, monkeypatch):
    from app.api import network_plan
    from sqlmodel.ext.asyncio.session import AsyncSession

    _, async_engine = engine
    monkeypatch.setattr(
        network_plan.network_agent_client,
        "read_status",
        AsyncMock(return_value=IPCResponse(
            request_id=uuid4(), ok=True,
            result=AgentStatus(state="idle", active_generation=2, last_known_good_generation=2),
        )),
    )
    monkeypatch.setattr(
        network_plan.network_agent_client,
        "validate",
        AsyncMock(side_effect=lambda plan: IPCResponse(
            request_id=uuid4(), ok=True,
            result=ValidationResult(
                generation_id=plan.generation_id,
                fingerprint=plan.fingerprint(),
                operation_count=len(plan.operations),
                permitted_egress_count=len(plan.egress_candidates),
            ),
        )),
    )

    async def inspect_twice():
        async with AsyncSession(async_engine) as first, AsyncSession(async_engine) as second:
            return await asyncio.gather(
                network_plan.validate_current_plan(first),
                network_plan.validate_current_plan(second),
            )

    first_response, second_response = asyncio.run(inspect_twice())
    first = first_response["plan"]
    second = second_response["plan"]
    assert first.generation == second.generation == 3
    assert first.base_generation == second.base_generation == 2
    assert first.generation_id != second.generation_id
    assert first.operations == second.operations
    assert network_plan.network_agent_client.validate.await_count == 2
    assert network_plan.network_agent_client.read_status.await_count == 2


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


@pytest.mark.asyncio
@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"),
    reason="SO_PEERCRED transport requires Linux",
)
async def test_typed_client_preserves_agent_ipc_rejection(tmp_path):
    from app.network_agent.client import NetworkAgentRejected

    path = tmp_path / "reject.sock"
    agent = MockNetworkAgent(foreign_objects={
        "tempest-ingress-default-block": "operator",
    })
    server = AgentServer(
        path,
        agent=agent,
        peer_policy=PeerPolicy(frozenset({os.getuid()}), frozenset({os.getgid()})),
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        client = NetworkAgentClient(str(path))
        with pytest.raises(NetworkAgentRejected) as error:
            await client.validate(make_plan())
        assert error.value.code == "ownership_conflict"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_typed_client_rejects_method_mismatched_result():
    from app.network_agent.client import NetworkAgentUnavailable

    response = IPCResponse(
        request_id=uuid4(), ok=True,
        result=AgentStatus(state="idle", active_generation=0, last_known_good_generation=0),
    )
    with pytest.raises(NetworkAgentUnavailable, match="invalid validate result"):
        NetworkAgentClient._expect_result(response, ValidationResult, "validate")
