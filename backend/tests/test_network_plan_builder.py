from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from app.network_agent.models import AgentStatus, EnsureRoute, SetEgressPolicy
from app.network_agent.plan_builder import (
    NetworkPlanBuildConflict,
    NetworkPlanBuildError,
    build_network_plan,
)


def _node(node_id, protocol="vless", enabled=True):
    return SimpleNamespace(
        id=node_id,
        protocol=protocol,
        enabled=enabled,
        name="credential-bearing name must not be serialized",
        address="secret.example",
        uuid="secret-token",
    )


def _status(**overrides):
    return AgentStatus(
        state=overrides.get("state", "idle"),
        active_generation=overrides.get("active_generation", 0),
        last_known_good_generation=overrides.get("last_known_good_generation", 0),
    )


def test_builder_maps_only_enabled_nodes_to_secret_free_candidates_and_blocks_ingress():
    plan = build_network_plan(
        [_node(8), _node(3, "wireguard"), _node(2, enabled=False)],
        {"active_node_id": "8"},
        _status(active_generation=4, last_known_good_generation=4),
        "tempest-ns-default",
    )

    assert plan.generation == 5
    assert plan.base_generation == 4
    assert plan.default_egress_id == "tempest-egress-node-8"
    assert [(item.egress_id, item.kind) for item in plan.egress_candidates] == [
        ("tempest-egress-node-3", "wireguard"),
        ("tempest-egress-node-8", "proxy"),
    ]
    policies = [op for op in plan.operations if isinstance(op, SetEgressPolicy)]
    assert len(policies) == 1 and policies[0].verdict == "block"
    assert not any(isinstance(op, EnsureRoute) for op in plan.operations)
    assert plan.direct_fallback_allowed is False
    assert "secret-token" not in plan.canonical_json()
    assert "secret.example" not in plan.canonical_json()


def test_builder_without_active_selection_uses_stable_lowest_node_id():
    plan = build_network_plan([_node(12), _node(4)], {}, _status(), "tempest-ns-default")
    assert plan.default_egress_id == "tempest-egress-node-4"


@pytest.mark.parametrize("nodes,stored_settings", [
    ([], {}),
    ([_node(1, enabled=False)], {}),
    ([_node(1)], {"active_node_id": "broken"}),
    ([_node(1)], {"active_node_id": "2"}),
    ([_node(1, "direct")], {}),
])
def test_builder_rejects_invalid_saved_configuration(nodes, stored_settings):
    with pytest.raises(NetworkPlanBuildError):
        build_network_plan(nodes, stored_settings, _status(), "tempest-ns-default")


@pytest.mark.parametrize("state", ["pending_confirmation", "degraded"])
def test_builder_refuses_to_compete_with_agent_state(state):
    with pytest.raises(NetworkPlanBuildConflict):
        build_network_plan([_node(1)], {}, _status(state=state), "tempest-ns-default")


def test_builder_rejects_unprepared_namespace_id():
    with pytest.raises(NetworkPlanBuildError, match="NetworkPlan v1"):
        build_network_plan([_node(1)], {}, _status(), "host-network")


def test_concurrent_inspection_plans_are_independent_and_do_not_claim_agent_generation():
    status = _status(active_generation=7, last_known_good_generation=7)
    with ThreadPoolExecutor(max_workers=8) as pool:
        plans = list(pool.map(
            lambda _: build_network_plan([_node(1)], {}, status, "tempest-ns-default"),
            range(16),
        ))

    assert {plan.generation for plan in plans} == {8}
    assert {plan.base_generation for plan in plans} == {7}
    assert len({plan.generation_id for plan in plans}) == len(plans)
    assert all(plan.operations[0].verdict == "block" for plan in plans)
