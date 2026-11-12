"""Service-level guards for legacy operations outside Network Agent v1.

HTTP middleware is only an outer boundary. These guards keep direct calls from
startup hooks, schedulers, tests, and other internal services from reaching a
legacy side effect while the Control Plane runs in Agent mode.
"""

from app.config import settings
from app.network_agent.client import NetworkApplyUnsupported


class EngineLifecycleUnsupported(NetworkApplyUnsupported):
    """The separately owned Xray/engine lifecycle is not migrated yet."""


def require_legacy_network(operation: str) -> None:
    """Reject an unmigrated host-network operation before its first effect."""
    if settings.control_plane_mode == "agent":
        raise NetworkApplyUnsupported(
            f"{operation} is unavailable in Agent mode until owned NetworkPlan apply exists"
        )


def require_legacy_engine(operation: str) -> None:
    """Keep engine lifecycle separate from NetworkPlan and host commands."""
    if settings.control_plane_mode == "agent":
        raise EngineLifecycleUnsupported(
            f"{operation} requires the separately defined engine lifecycle boundary"
        )


def require_legacy_host_operation(operation: str) -> None:
    """Reject other privileged host hooks, such as the external updater."""
    if settings.control_plane_mode == "agent":
        raise NetworkApplyUnsupported(
            f"{operation} is unavailable in Agent Control Plane mode"
        )
