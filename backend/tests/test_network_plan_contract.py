from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.network_agent.mock import MockNetworkAgent, NetworkAgentError, PlanConflict
from app.network_agent.models import (
    ApplyRequest,
    ConfirmRequest,
    EgressCandidate,
    EnsureInterface,
    EnsureRoute,
    IPCProtocolError,
    NetworkPlan,
    ReadStatusRequest,
    SetEgressPolicy,
    StageRequest,
    VerifyRequest,
    parse_ipc_request,
)


def make_plan(
    *,
    generation: int = 1,
    base_generation: int = 0,
    generation_id: UUID | None = None,
    egress_candidates: tuple[EgressCandidate, ...] | None = None,
    default_egress_id: str = "tempest-egress-primary",
    operations: tuple[object, ...] | None = None,
    direct_fallback_allowed: bool = False,
    confirmation_timeout_seconds: int = 10,
) -> NetworkPlan:
    chosen_id = generation_id or UUID(int=generation)
    candidates = egress_candidates if egress_candidates is not None else (
        EgressCandidate(egress_id="tempest-egress-primary", kind="wireguard"),
    )
    selected_operations = operations if operations is not None else (
        EnsureInterface(
            kind="ensure_interface",
            object={"owner": "tempest", "object_type": "interface", "object_id": "tempest-uplink"},
            namespace_id="tempest-ns-dataplane",
            interface_kind="dummy",
        ),
        EnsureRoute(
            kind="ensure_route",
            object={"owner": "tempest", "object_type": "route", "object_id": "tempest-egress-route"},
            interface_id="tempest-uplink",
            destination="0.0.0.0/0",
            egress_id=default_egress_id,
            table_id=1001,
        ),
        SetEgressPolicy(
            kind="set_egress_policy",
            object={"owner": "tempest", "object_type": "firewall_policy", "object_id": "tempest-ingress-default-block"},
            traffic_scope="protected_ingress",
            verdict="block",
        ),
        SetEgressPolicy(
            kind="set_egress_policy",
            object={"owner": "tempest", "object_type": "firewall_policy", "object_id": "tempest-ingress-allow-primary"},
            traffic_scope="protected_ingress",
            verdict="allow",
            egress_id=default_egress_id,
        ),
    )
    return NetworkPlan(
        generation_id=chosen_id,
        generation=generation,
        base_generation=base_generation,
        target_namespace_id="tempest-ns-dataplane",
        egress_candidates=candidates,
        default_egress_id=default_egress_id,
        direct_fallback_allowed=direct_fallback_allowed,
        confirmation_timeout_seconds=confirmation_timeout_seconds,
        operations=selected_operations,
    )


def test_plan_serialization_is_canonical_and_versioned() -> None:
    plan = make_plan()
    wire = plan.canonical_json()
    parsed = NetworkPlan.model_validate_json(wire)

    assert parsed.schema_version == 1
    assert parsed == plan
    assert parsed.fingerprint() == plan.fingerprint()
    assert len(plan.fingerprint()) == 64


@pytest.mark.parametrize(
    "overrides",
    [
        {"egress_candidates": ()},
        {
            "egress_candidates": (
                EgressCandidate(egress_id="tempest-egress-off", kind="wireguard", enabled=False),
            ),
        },
        {
            "egress_candidates": (
                EgressCandidate(egress_id="tempest-egress-primary", kind="direct"),
            ),
            "default_egress_id": "tempest-egress-primary",
        },
        {"default_egress_id": "tempest-egress-unknown"},
        {"generation": 1, "base_generation": 1},
    ],
)
def test_plan_rejects_missing_or_unsafe_egress_and_generation(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_plan(**overrides)


def test_plan_rejects_invalid_address_and_namespace_escape() -> None:
    bad_address = {
        "kind": "assign_address",
        "object": {"owner": "tempest", "object_type": "address", "object_id": "tempest-ip"},
        "interface_id": "tempest-uplink",
        "cidr": "not-an-address",
    }
    with pytest.raises(ValidationError):
        make_plan(operations=(bad_address,))

    bad_namespace = EnsureInterface(
        kind="ensure_interface",
        object={"owner": "tempest", "object_type": "interface", "object_id": "tempest-uplink"},
        namespace_id="tempest-ns-other",
        interface_kind="dummy",
    )
    with pytest.raises(ValidationError, match="plan namespace"):
        make_plan(operations=(bad_namespace,))


def test_route_rejects_gateway_address_family_mismatch() -> None:
    with pytest.raises(ValidationError, match="address family"):
        make_plan(
            operations=(
                make_plan().operations[0],
                {
                    "kind": "ensure_route",
                    "object": {"owner": "tempest", "object_type": "route", "object_id": "tempest-egress-route"},
                    "interface_id": "tempest-uplink",
                    "destination": "2001:db8::/32",
                    "egress_id": "tempest-egress-primary",
                    "table_id": 1001,
                    "gateway": "192.0.2.1",
                },
            )
        )


def test_plan_rejects_duplicate_owned_objects_and_duplicate_egress_ids() -> None:
    duplicate_route = EnsureRoute(
        kind="ensure_route",
        object={"owner": "tempest", "object_type": "route", "object_id": "tempest-uplink"},
        interface_id="tempest-uplink",
        destination="0.0.0.0/0",
        egress_id="tempest-egress-primary",
        table_id=1001,
    )
    with pytest.raises(ValidationError, match="more than once"):
        make_plan(operations=(make_plan().operations[0], duplicate_route))

    duplicate_candidate = EgressCandidate(
        egress_id="tempest-egress-primary", kind="wireguard"
    )
    with pytest.raises(ValidationError, match="candidate IDs must be unique"):
        make_plan(egress_candidates=(duplicate_candidate, duplicate_candidate))


def test_plan_requires_default_block_and_a_route_for_each_allowed_egress() -> None:
    plan = make_plan()
    no_default_block = tuple(
        operation
        for operation in plan.operations
        if not (isinstance(operation, SetEgressPolicy) and operation.verdict == "block")
    )
    with pytest.raises(ValidationError, match="default block policy"):
        make_plan(operations=no_default_block)

    no_route_for_allow = tuple(
        operation
        for operation in plan.operations
        if not isinstance(operation, EnsureRoute)
    )
    with pytest.raises(ValidationError, match="requires a route"):
        make_plan(operations=no_route_for_allow)


def test_unknown_operation_and_untyped_command_are_rejected() -> None:
    payload = json.loads(make_plan().canonical_json())
    payload["operations"][0] = {
        "kind": "run_shell",
        "command": "ip route replace default",
    }

    with pytest.raises(ValidationError):
        NetworkPlan.model_validate(payload)


def test_foreign_object_collision_is_rejected() -> None:
    agent = MockNetworkAgent(foreign_objects={"tempest-uplink": "operator"})

    with pytest.raises(NetworkAgentError, match="foreign owner") as error:
        agent.stage(make_plan())

    assert error.value.code == "ownership_conflict"


def test_generation_number_cannot_be_reused_with_different_plan() -> None:
    agent = MockNetworkAgent()
    first = make_plan(generation_id=UUID(int=101))
    second = make_plan(generation_id=UUID(int=102))

    agent.stage(first)
    with pytest.raises(PlanConflict, match="generation number"):
        agent.stage(second)


def test_same_generation_id_cannot_be_replayed_with_modified_content() -> None:
    agent = MockNetworkAgent()
    first = make_plan()
    first_interface = first.operations[0]
    assert isinstance(first_interface, EnsureInterface)
    changed = make_plan(
        operations=(first_interface.model_copy(update={"interface_kind": "tun"}), *first.operations[1:]),
    )

    agent.stage(first)
    with pytest.raises(PlanConflict, match="reserved"):
        agent.stage(changed)


def test_apply_is_idempotent_and_confirm_commits_last_known_good() -> None:
    agent = MockNetworkAgent()
    plan = make_plan()
    agent.stage(plan)

    applied = agent.apply(plan.generation_id)
    repeated = agent.apply(plan.generation_id)
    verified = agent.verify(plan.generation_id)
    repeated_verify = agent.verify(plan.generation_id)
    confirmed = agent.confirm(plan.generation_id)
    repeated_confirm = agent.confirm(plan.generation_id)

    assert applied.state == "pending_confirmation"
    assert repeated == applied
    assert "tempest-ingress-default-block" in applied.owned_object_ids
    assert repeated_verify == verified
    assert confirmed.active_generation == 1
    assert confirmed.last_known_good_generation == 1
    assert confirmed.state == "idle"
    assert "tempest-ingress-default-block" in confirmed.owned_object_ids
    assert repeated_confirm == confirmed


def test_confirm_before_successful_verify_is_rejected() -> None:
    agent = MockNetworkAgent()
    plan = make_plan()
    agent.stage(plan)
    agent.apply(plan.generation_id)

    with pytest.raises(PlanConflict, match="must pass Verify"):
        agent.confirm(plan.generation_id)

    assert agent.read_status().pending_generation_id == plan.generation_id


@pytest.mark.parametrize("verification", ["stale", "failed"])
def test_failed_or_stale_verification_never_authorizes_confirm(verification: str) -> None:
    agent = MockNetworkAgent()
    plan = make_plan()
    agent.stage(plan)
    agent.apply(plan.generation_id)

    if verification == "stale":
        with pytest.raises(NetworkAgentError):
            agent.verify(UUID(int=999))
    else:
        # Simulate an integrity mismatch between active state and the pending snapshot.
        agent._active = replace(agent._active, objects={})
        with pytest.raises(PlanConflict, match="differs from pending"):
            agent.verify(plan.generation_id)

    with pytest.raises(PlanConflict, match="must pass Verify"):
        agent.confirm(plan.generation_id)


def test_failed_reverification_revokes_prior_permission() -> None:
    agent = MockNetworkAgent()
    plan = make_plan()
    agent.stage(plan)
    agent.apply(plan.generation_id)
    agent.verify(plan.generation_id)
    agent._active = replace(agent._active, objects={})

    with pytest.raises(PlanConflict, match="differs from pending"):
        agent.verify(plan.generation_id)
    with pytest.raises(PlanConflict, match="must pass Verify"):
        agent.confirm(plan.generation_id)


def test_rollback_clears_verification_permission() -> None:
    agent = MockNetworkAgent()
    plan = make_plan()
    agent.stage(plan)
    agent.apply(plan.generation_id)
    agent.verify(plan.generation_id)
    agent.rollback(plan.generation_id, "verification no longer trusted")

    with pytest.raises(NetworkAgentError):
        agent.confirm(plan.generation_id)


def test_parallel_apply_allows_only_one_unconfirmed_generation() -> None:
    agent = MockNetworkAgent()
    first = make_plan(generation=1, generation_id=UUID(int=201))
    second = make_plan(generation=2, generation_id=UUID(int=202))
    agent.stage(first)
    agent.stage(second)

    def try_apply(generation_id: UUID) -> str:
        try:
            agent.apply(generation_id)
            return "applied"
        except NetworkAgentError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(try_apply, (first.generation_id, second.generation_id)))

    assert sorted(outcomes) == ["applied", "conflict"]
    assert agent.read_status().state == "pending_confirmation"


def test_confirmation_deadline_restores_last_known_good_fail_closed_state() -> None:
    now = [1000.0]
    agent = MockNetworkAgent(clock=lambda: now[0])
    plan = make_plan(confirmation_timeout_seconds=5)
    agent.stage(plan)
    agent.apply(plan.generation_id)
    agent.verify(plan.generation_id)

    now[0] += 6
    status = agent.read_status()

    assert status.state == "degraded"
    assert status.active_generation == 0
    assert status.last_known_good_generation == 0
    assert status.pending_generation_id is None
    assert status.owned_object_ids == ()
    assert "last-known-good restored" in (status.last_error or "")
    with pytest.raises(NetworkAgentError):
        agent.confirm(plan.generation_id)


def test_explicit_rollback_restores_last_known_good() -> None:
    agent = MockNetworkAgent()
    plan = make_plan()
    agent.stage(plan)
    agent.apply(plan.generation_id)
    agent.verify(plan.generation_id)

    status = agent.rollback(plan.generation_id, "contract test")

    assert status.state == "degraded"
    assert status.active_generation == status.last_known_good_generation == 0
    assert status.pending_generation_id is None
    assert status.owned_object_ids == ()


def test_invalid_plan_without_egress_never_reaches_agent_state() -> None:
    agent = MockNetworkAgent()
    payload = json.loads(make_plan().canonical_json())
    payload["egress_candidates"] = []

    with pytest.raises(ValidationError):
        NetworkPlan.model_validate(payload)

    status = agent.read_status()
    assert status.state == "idle"
    assert status.active_generation == 0
    assert status.owned_object_ids == ()


def test_typed_ipc_round_trip_and_audit_do_not_trust_actor_fields() -> None:
    plan = make_plan()
    request = StageRequest(method="stage", request_id=UUID(int=301), plan=plan)
    parsed = parse_ipc_request(request.model_dump_json())
    assert parsed == request

    agent = MockNetworkAgent()
    response = agent.handle_request(request.model_dump_json())

    assert response.ok is True
    assert response.request_id == request.request_id
    assert response.result is not None
    assert agent.audit_log[-1]["request_id"] == str(request.request_id)
    assert agent.audit_log[-1]["peer_uid"] is None

    status_request = ReadStatusRequest(method="read_status", request_id=UUID(int=302))
    status_response = agent.handle_request(status_request.model_dump_json())
    assert status_response.ok is True
    assert status_response.request_id == status_request.request_id


def test_ipc_apply_verify_confirm_lifecycle() -> None:
    plan = make_plan()
    agent = MockNetworkAgent()
    staged = agent.handle_request(
        StageRequest(method="stage", request_id=UUID(int=401), plan=plan).model_dump_json()
    )
    applied = agent.handle_request(
        ApplyRequest(method="apply", request_id=UUID(int=402), generation_id=plan.generation_id).model_dump_json()
    )
    verified = agent.handle_request(
        VerifyRequest(method="verify", request_id=UUID(int=403), generation_id=plan.generation_id).model_dump_json()
    )
    confirmed = agent.handle_request(
        ConfirmRequest(method="confirm", request_id=UUID(int=404), generation_id=plan.generation_id).model_dump_json()
    )

    assert staged.ok and applied.ok and verified.ok and confirmed.ok
    assert verified.result is not None and verified.result.state == "pending_confirmation"
    assert confirmed.result is not None and confirmed.result.last_known_good_generation == 1


def test_ipc_confirm_before_verify_returns_conflict() -> None:
    plan = make_plan()
    agent = MockNetworkAgent()
    agent.stage(plan)
    agent.apply(plan.generation_id)

    response = agent.handle_request(
        ConfirmRequest(method="confirm", request_id=UUID(int=405), generation_id=plan.generation_id).model_dump_json()
    )

    assert response.ok is False
    assert response.error is not None and response.error.code == "conflict"


def test_unknown_ipc_method_and_oversized_frame_are_rejected() -> None:
    with pytest.raises(IPCProtocolError):
        parse_ipc_request('{"protocol_version":1,"request_id":"00000000-0000-0000-0000-000000000001","method":"exec","command":"id"}')
    with pytest.raises(IPCProtocolError, match="size limit"):
        parse_ipc_request(b" " * (64 * 1024 + 1))
