"""Security boundary tests for the no-op local development profile."""
import re
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_safe_lifespan_skips_operational_startup(monkeypatch):
    from app.main import app, lifespan
    from app.api import system
    from app.core import (
        autocheck_scheduler,
        chain_drift,
        circle_scheduler,
        device_scanner,
        dns_logger,
        domain_stats,
        events,
        geo_scheduler,
        healthcheck,
        jobs,
        metrics_collector,
        naive_manager,
        naive_supervisor,
        router_mode,
        router_watchdog,
        sub_scheduler,
        xray,
    )

    monkeypatch.setattr(settings, "dev_safe_mode", True)
    hooks = (
        (xray.xray_manager, "get_version", True),
        (xray.xray_manager, "start", True),
        (xray.xray_manager, "stop", True),
        (healthcheck.health_checker, "start", False),
        (sub_scheduler.subscription_scheduler, "start", False),
        (circle_scheduler.circle_scheduler, "start", False),
        (device_scanner.device_scanner, "start", False),
        (metrics_collector.metrics_collector, "start", False),
        (geo_scheduler.geo_scheduler, "start", False),
        (autocheck_scheduler.autocheck_scheduler, "start", False),
        (chain_drift.chain_drift_scheduler, "start", False),
        (domain_stats.domain_stats_scheduler, "start", False),
        (router_mode, "reconcile_on_boot", True),
        (router_watchdog, "recover_on_boot", True),
        (router_watchdog.router_watchdog, "start", False),
        (naive_supervisor.naive_supervisor, "start", False),
        (naive_manager.naive_manager, "sync_all", True),
        (dns_logger, "start_trim_task", False),
        (events, "start_trim_task", False),
        (jobs.job_manager, "start", True),
        (system, "apply_system_toggles_on_boot", True),
    )
    with (
        ExitStack() as stack,
        patch("app.main.create_db_and_tables") as create_db,
        patch("app.main.init_default_settings", new_callable=AsyncMock) as init_defaults,
        patch("app.main._reapply_limits", new_callable=AsyncMock) as reapply_limits,
    ):
        hook_mocks = [
            stack.enter_context(
                patch.object(target, attribute, new_callable=AsyncMock if is_async else None)
            )
            for target, attribute, is_async in hooks
        ]
        async with lifespan(app):
            pass

    create_db.assert_called_once_with()
    init_defaults.assert_awaited_once_with()
    reapply_limits.assert_not_called()
    for hook, mock in zip(hooks, hook_mocks):
        if hook[2]:
            mock.assert_not_awaited()
        else:
            mock.assert_not_called()


def test_safe_mode_denies_network_and_host_operations(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_safe_mode", True)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["vpn_traffic_available"] is False

    status = client.get("/api/dev/safe-mode")
    assert status.status_code == 200
    assert status.json()["dataplane"] == "mock-noop"
    assert status.json()["vpn_traffic_available"] is False

    for method, path in (
        ("post", "/api/system/start"),
        ("post", "/api/system/stop"),
        ("post", "/api/system/restart"),
        ("post", "/api/system/reload-config"),
        ("post", "/api/system/mode"),
        ("post", "/api/system/active-node"),
        ("patch", "/api/system/settings"),
        ("post", "/api/network/apply"),
        ("post", "/api/system/backup/preview"),
        ("post", "/api/system/backup/restore"),
        ("post", "/api/nodes/1/check"),
        ("post", "/api/subscriptions/1/refresh"),
        ("post", "/api/autocheck/run"),
        ("post", "/api/servers/1/deploy"),
        ("post", "/api/system/update/start"),
        ("get", "/api/diagnostics/network"),
    ):
        response = client.request(
            method,
            path,
            json={} if method not in {"GET", "HEAD", "OPTIONS"} else None,
        )
        assert response.status_code == 423, (method, path, response.text)
        assert response.json()["dataplane"] == "mock-noop"


def test_application_brand_is_tempest():
    from app.main import app

    assert app.title == "TEMPEST API"


def test_safe_mode_denies_every_registered_non_allowlisted_http_operation(
    client, monkeypatch
):
    """New and alternate API routes fail closed unless explicitly reviewed."""
    from app.core.dev_safe_mode import request_is_allowed
    from app.main import app

    monkeypatch.setattr(settings, "dev_safe_mode", True)
    checked = 0
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path_template = getattr(route, "path", None)
        if not methods or not path_template:
            continue
        path = re.sub(r"\{[^}]+\}", "1", path_template)
        for method in methods:
            if request_is_allowed(method, path):
                continue
            response = client.request(
                method,
                path,
                json={} if method not in {"GET", "HEAD", "OPTIONS"} else None,
            )
            assert response.status_code == 423, (
                method,
                path_template,
                response.status_code,
                response.text,
            )
            checked += 1
    assert checked >= 30, f"Expected broad route coverage, found {checked} operations"


def test_safe_mode_rejects_all_websocket_routes(client, monkeypatch):
    from starlette.routing import WebSocketRoute
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    monkeypatch.setattr(settings, "dev_safe_mode", True)
    routes = [route for route in app.routes if isinstance(route, WebSocketRoute)]
    assert routes, "Expected to audit the registered WebSocket surfaces"
    for route in routes:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(route.path):
                pass
        assert closed.value.code == 1008, route.path


def test_safe_mode_user_agent_crud_is_database_only(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "dev_safe_mode", True)
    created = client.post(
        "/api/user-agents",
        headers=auth_headers,
        json={
            "key": "safe-dev-agent",
            "name": "Safe Dev Agent",
            "user_agent": "TEMPEST development test agent",
        },
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    changed = client.patch(
        f"/api/user-agents/{template_id}",
        headers=auth_headers,
        json={"name": "Safe Dev Agent Updated"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Safe Dev Agent Updated"

    removed = client.delete(
        f"/api/user-agents/{template_id}", headers=auth_headers
    )
    assert removed.status_code == 204


def test_safe_mode_node_crud_does_not_start_naive_sidecar(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "dev_safe_mode", True)
    with (
        patch("app.api.nodes._ensure_naive_port", new_callable=AsyncMock) as ensure_port,
        patch("app.api.nodes._sync_naive_sidecar", new_callable=AsyncMock) as sidecar,
        patch("app.api.nodes._refresh_naive_tproxy_bypass", new_callable=AsyncMock) as nft,
    ):
        created = client.post(
            "/api/nodes",
            headers=auth_headers,
            json={
                "name": "Mock Naive node",
                "protocol": "naive",
                "address": "example.invalid",
                "port": 443,
            },
        )

    assert created.status_code == 201, created.text
    ensure_port.assert_not_awaited()
    sidecar.assert_not_awaited()
    nft.assert_not_awaited()


def test_safe_mode_settings_read_skips_host_probes(
    client, auth_headers, default_settings, monkeypatch
):
    monkeypatch.setattr(settings, "dev_safe_mode", True)
    with (
        patch("app.api.system._detect_ip", side_effect=AssertionError("host probe")),
        patch("app.api.system._detect_cidr", side_effect=AssertionError("host probe")),
        patch("app.api.system._detect_sysctl_bool", side_effect=AssertionError("sysctl read")),
        patch("app.api.system._detect_resolv_use_vc", side_effect=AssertionError("DNS read")),
    ):
        response = client.get("/api/system/settings", headers=auth_headers)

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_safe_mode_dataplane_dispatch_is_noop(monkeypatch):
    from app.core import dataplane

    monkeypatch.setattr(settings, "dev_safe_mode", True)
    with patch.object(dataplane, "apply_dataplane", new_callable=AsyncMock) as apply:
        await dataplane.dispatch_dataplane(None, "start")
    apply.assert_not_awaited()
    # The direct path has its own guard, independent of dispatch callers.
    await dataplane.apply_dataplane("stop")


@pytest.mark.asyncio
async def test_safe_mode_nftables_apply_is_noop(monkeypatch):
    from app.api import system

    monkeypatch.setattr(settings, "dev_safe_mode", True)
    with patch(
        "app.core.nftables.nftables_manager.apply",
        new_callable=AsyncMock,
    ) as apply:
        result = await system._apply_nftables(None, {"inbound_mode": "tproxy"})

    assert result == "tproxy"
    apply.assert_not_awaited()
