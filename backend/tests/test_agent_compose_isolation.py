"""Static security assertions for the Agent-mode Control Plane container."""

from pathlib import Path

import yaml
import pytest

from app.network_agent.mock import MockNetworkAgent
import app.network_agent.service as agent_service


ROOT = Path(__file__).parents[2]
AGENT_COMPOSE = ROOT / "docker-compose.agent.yml"


def _agent_service() -> dict:
    config = yaml.safe_load(AGENT_COMPOSE.read_text(encoding="utf-8"))
    return config["services"]["api"]


def test_agent_compose_runs_unprivileged_and_drops_capabilities() -> None:
    service = _agent_service()
    environment = service["environment"]
    build = service["build"]

    assert environment["CONTROL_PLANE_MODE"] == "agent"
    assert environment["DEV_SAFE_MODE"] == "0"
    assert build["dockerfile"] == "Dockerfile"
    assert build["target"] == "agent"
    assert service["read_only"] is True
    assert service["user"].startswith("10001:")
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert service.get("privileged", False) is False
    assert service.get("network_mode") != "host"
    assert service.get("pid") != "host"
    assert "devices" not in service
    assert "no-new-privileges:true" in service["security_opt"]


def test_agent_compose_mounts_only_agent_ipc_and_its_database() -> None:
    service = _agent_service()
    mounts = service["volumes"]
    bind_mounts = [mount for mount in mounts if isinstance(mount, dict)]

    assert len(bind_mounts) == 1
    agent_socket = bind_mounts[0]
    assert agent_socket == {
        "type": "bind",
        "source": "/run/tempest-agent/control.sock",
        "target": "/run/tempest-agent/control.sock",
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    assert all(isinstance(mount, dict) or mount.startswith("tempest_agent_db:") for mount in mounts)
    assert service["environment"]["DOCKER_HOST"] == "unix:///run/tempest-no-docker.sock"


def test_agent_image_uses_a_non_root_runtime_user() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    agent_stage = dockerfile.split("FROM production AS agent", maxsplit=1)[1]

    assert "USER 10001:10001" in agent_stage
    assert "chown -R 10001:10001 /tmp/tempest /app/data" in agent_stage


def test_agent_ipc_service_fails_closed_without_linux_transport(monkeypatch) -> None:
    monkeypatch.setattr(agent_service, "_UNIX_STREAM_SERVER", None)

    with pytest.raises(RuntimeError, match="requires Linux AF_UNIX and SO_PEERCRED"):
        agent_service.AgentServer(
            "unused.sock",
            agent=MockNetworkAgent(),
            peer_policy=agent_service.PeerPolicy(frozenset({1}), frozenset({1})),
        )


def test_legacy_compose_is_explicitly_locked_to_legacy_mode() -> None:
    legacy = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    agent = yaml.safe_load(AGENT_COMPOSE.read_text(encoding="utf-8"))

    assert "CONTROL_PLANE_MODE=legacy" in legacy["services"]["backend"]["environment"]
    assert agent["services"]["api"]["environment"]["CONTROL_PLANE_MODE"] == "agent"
