"""Pure translation from PiTun's saved node selection to a safe v1 plan.

This deliberately emits only the protected-ingress deny baseline. The legacy
Node rows describe proxy endpoints, but do not yet define owned kernel routes,
interfaces, or an approved policy for allowing ingress traffic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from uuid import uuid4

from pydantic import ValidationError

from app.network_agent.models import (
    AgentStatus,
    EgressCandidate,
    NetworkPlan,
    OwnedObject,
    SetEgressPolicy,
)


class NetworkPlanBuildError(ValueError):
    """Stored Control Plane state cannot produce a safe inspectable plan."""


class NetworkPlanBuildConflict(NetworkPlanBuildError):
    """Agent state already has a generation in flight or needs recovery."""


_SUPPORTED_NODE_PROTOCOLS = {
    "vless", "vmess", "trojan", "ss", "wireguard", "socks", "hy2", "naive",
}


def build_network_plan(
    nodes: Iterable[object],
    settings: Mapping[str, str],
    agent_status: AgentStatus,
    target_namespace_id: str,
) -> NetworkPlan:
    """Build a secret-free plan from already-loaded state.

    Only enabled DB nodes become candidate metadata. There is no implicit
    DIRECT candidate and no allow/route operation until those semantics are
    explicitly migrated. Agent status supplies the compare-and-swap base.
    """
    if agent_status.state != "idle":
        raise NetworkPlanBuildConflict(
            f"Agent is {agent_status.state}; refusing to build a competing plan"
        )

    enabled_nodes = sorted(
        (node for node in nodes if bool(getattr(node, "enabled", False))),
        key=lambda node: (getattr(node, "id", None) is None, getattr(node, "id", 0) or 0),
    )
    if not enabled_nodes:
        raise NetworkPlanBuildError("no enabled egress nodes are configured")
    if len(enabled_nodes) > 64:
        raise NetworkPlanBuildError("more than 64 enabled egress nodes are configured")

    candidates: list[EgressCandidate] = []
    node_ids: set[int] = set()
    for node in enabled_nodes:
        node_id = getattr(node, "id", None)
        if (
            isinstance(node_id, bool)
            or not isinstance(node_id, int)
            or node_id < 1
            or node_id in node_ids
        ):
            raise NetworkPlanBuildError("enabled egress nodes have invalid or duplicate IDs")
        node_ids.add(node_id)
        protocol = str(getattr(node, "protocol", "")).strip().lower()
        if protocol not in _SUPPORTED_NODE_PROTOCOLS:
            raise NetworkPlanBuildError(
                f"enabled egress node {node_id} has an unsupported protocol"
            )
        candidates.append(
            EgressCandidate(
                egress_id=f"tempest-egress-node-{node_id}",
                kind="wireguard" if protocol == "wireguard" else "proxy",
                enabled=True,
            )
        )

    active_node_value = settings.get("active_node_id", "").strip()
    if active_node_value:
        try:
            active_node_id = int(active_node_value)
        except ValueError as exc:
            raise NetworkPlanBuildError("active_node_id is not a valid integer") from exc
        if active_node_id not in node_ids:
            raise NetworkPlanBuildError(
                "active_node_id does not reference an enabled egress node"
            )
    else:
        active_node_id = min(node_ids)

    try:
        return NetworkPlan(
            generation_id=uuid4(),
            generation=agent_status.active_generation + 1,
            base_generation=agent_status.active_generation,
            target_namespace_id=target_namespace_id,
            egress_candidates=tuple(candidates),
            default_egress_id=f"tempest-egress-node-{active_node_id}",
            direct_fallback_allowed=False,
            operations=(
                SetEgressPolicy(
                    kind="set_egress_policy",
                    object=OwnedObject(
                        owner="tempest",
                        object_type="firewall_policy",
                        object_id="tempest-policy-protected-ingress",
                    ),
                    traffic_scope="protected_ingress",
                    verdict="block",
                ),
            ),
        )
    except ValidationError as exc:
        raise NetworkPlanBuildError("saved settings do not satisfy NetworkPlan v1") from exc
