"""Privilege-free ownership registry checks; no Linux networking is touched."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from app.network_agent.registry import (
    ClaimKind,
    InMemoryNetworkInventory,
    NetworkObject,
    NetworkObjectRegistry,
    ObjectKind,
    RegistryConflict,
    ResourceClaim,
)


NS = "tempest-ns-test"
FP = "a" * 64


def obj(
    object_id: str, kind: ObjectKind, claim_kind: ClaimKind, claim_value: str,
    generation_id, generation: int, *, fingerprint: str = FP,
    namespace_id: str = NS, extra: tuple[ResourceClaim, ...] = (),
) -> NetworkObject:
    return NetworkObject(
        object_id, kind, namespace_id, "tempest", generation_id, generation,
        (ResourceClaim(claim_kind, claim_value), *extra), fingerprint,
    )


def registry():
    inventory = InMemoryNetworkInventory()
    return NetworkObjectRegistry(NS, inventory), inventory


def test_create_and_repeat_same_generation_without_new_proof():
    reg, inventory = registry()
    gen = uuid4()
    table = obj("tempest-table", ObjectKind.NFT_TABLE, ClaimKind.NFT_TABLE_NAME, "inet:tempest", gen, 1)
    first = reg.reconcile_mock(gen, 1, 0, (table,))
    proof = reg.confirmed[0].proof
    revision = inventory.snapshot().revision
    assert first.added == (table.object_id,)
    assert proof
    assert reg.reconcile_mock(gen, 1, 0, (table,)).added == ()
    assert inventory.snapshot().revision == revision
    assert reg.confirmed[0].proof == proof


@pytest.mark.parametrize("same_id", [True, False])
def test_foreign_collision_cannot_be_adopted_by_name_or_id(same_id):
    reg, inventory = registry()
    gen = uuid4()
    foreign = obj("tempest-foreign", ObjectKind.NFT_TABLE, ClaimKind.NFT_TABLE_NAME, "inet:tempest", gen, 1)
    inventory.seed_foreign(foreign)
    requested = replace(foreign, object_id=foreign.object_id if same_id else "tempest-new")
    with pytest.raises(RegistryConflict):
        reg.reconcile_mock(gen, 1, 0, (requested,))
    assert reg.generation == 0
    assert inventory.snapshot().objects[0].proof is None


def test_foreign_object_is_never_deleted_by_an_empty_desired_generation():
    reg, inventory = registry()
    foreign = obj("tempest-foreign", ObjectKind.NFT_TABLE, ClaimKind.NFT_TABLE_NAME, "inet:foreign", uuid4(), 1)
    inventory.seed_foreign(foreign)
    reg.reconcile_mock(uuid4(), 1, 0, ())
    assert inventory.snapshot().objects[0].object_id == foreign.object_id
    assert inventory.snapshot().objects[0].proof is None


def test_removal_rechecks_proof_after_preview_and_fails_closed():
    reg, inventory = registry()
    first_id = uuid4()
    link = obj("tempest-link", ObjectKind.TUN_INTERFACE, ClaimKind.INTERFACE_NAME, "tun0", first_id, 1)
    reg.reconcile_mock(first_id, 1, 0, (link,))
    next_id = uuid4()
    assert reg.diff(next_id, 2, 1, ()).removed == (link.object_id,)
    inventory.alter_externally(link.object_id, lose_proof=True)
    with pytest.raises(RegistryConflict, match="proof or drifted"):
        reg.reconcile_mock(next_id, 2, 1, ())
    assert reg.generation == 1
    assert inventory.snapshot().objects[0].object_id == link.object_id


def test_external_configuration_drift_blocks_update_and_repeat():
    reg, inventory = registry()
    first_id = uuid4()
    link = obj("tempest-link", ObjectKind.WIREGUARD_INTERFACE, ClaimKind.INTERFACE_NAME, "wg0", first_id, 1)
    reg.reconcile_mock(first_id, 1, 0, (link,))
    inventory.alter_externally(link.object_id, fingerprint="b" * 64)
    next_id = uuid4()
    with pytest.raises(RegistryConflict, match="proof or drifted"):
        reg.diff(next_id, 2, 1, (replace(link, generation_id=next_id, generation=2),))
    with pytest.raises(RegistryConflict, match="proof or drifted"):
        reg.reconcile_mock(first_id, 1, 0, (link,))


@pytest.mark.parametrize(
    ("kind", "claim", "value"),
    [
        (ObjectKind.ROUTING_TABLE, ClaimKind.ROUTING_TABLE_ID, "100"),
        (ObjectKind.WIREGUARD_INTERFACE, ClaimKind.INTERFACE_NAME, "wg-test"),
        (ObjectKind.TUN_INTERFACE, ClaimKind.INTERFACE_NAME, "tun-test"),
    ],
)
def test_exclusive_resource_ids_conflict(kind, claim, value):
    reg, inventory = registry()
    gen = uuid4()
    inventory.seed_foreign(obj("tempest-foreign", kind, claim, value, gen, 1))
    with pytest.raises(RegistryConflict, match="resource claim"):
        reg.reconcile_mock(gen, 1, 0, (obj("tempest-new", kind, claim, value, gen, 1),))


@pytest.mark.parametrize("claim,value", [(ClaimKind.IP_RULE_PRIORITY, "100"), (ClaimKind.FWMARK, "42")])
def test_ip_rule_priority_and_fwmark_conflict(claim, value):
    reg, inventory = registry()
    gen = uuid4()
    foreign = obj("tempest-foreign", ObjectKind.IP_RULE, ClaimKind.IP_RULE_PRIORITY,
                  "100", gen, 1, extra=(ResourceClaim(ClaimKind.FWMARK, "42"),))
    inventory.seed_foreign(foreign)
    priority = "100" if claim == ClaimKind.IP_RULE_PRIORITY else "101"
    mark = "42" if claim == ClaimKind.FWMARK else "43"
    desired = obj("tempest-new", ObjectKind.IP_RULE, ClaimKind.IP_RULE_PRIORITY,
                  priority, gen, 1, extra=(ResourceClaim(ClaimKind.FWMARK, mark),))
    with pytest.raises(RegistryConflict, match="resource claim"):
        reg.reconcile_mock(gen, 1, 0, (desired,))


def test_generation_diff_and_stable_identity():
    reg, _ = registry()
    first_id = uuid4()
    table = obj("tempest-table", ObjectKind.ROUTING_TABLE, ClaimKind.ROUTING_TABLE_ID, "100", first_id, 1)
    old = obj("tempest-old", ObjectKind.MANAGED_INTERFACE, ClaimKind.INTERFACE_NAME, "old0", first_id, 1)
    reg.reconcile_mock(first_id, 1, 0, (table, old))
    next_id = uuid4()
    changed = replace(table, generation_id=next_id, generation=2, fingerprint="b" * 64)
    added = obj("tempest-new", ObjectKind.NFT_CHAIN, ClaimKind.NFT_CHAIN_KEY, "inet:tempest:forward", next_id, 2)
    diff = reg.diff(next_id, 2, 1, (changed, added))
    assert diff.added == ("tempest-new",)
    assert diff.changed == ("tempest-table",)
    assert diff.removed == ("tempest-old",)
    assert reg.generation == 1  # Diff has no side effects.
    assert reg.reconcile_mock(next_id, 2, 1, (changed, added)) == diff
    assert {item.object_id for item in reg.confirmed} == {"tempest-table", "tempest-new"}


def test_competing_generations_and_changed_retry_rejected():
    reg, _ = registry()
    gen = uuid4()
    link = obj("tempest-link", ObjectKind.TUN_INTERFACE, ClaimKind.INTERFACE_NAME, "tun0", gen, 1)
    reg.reconcile_mock(gen, 1, 0, (link,))
    with pytest.raises(RegistryConflict, match="stale or competing"):
        reg.reconcile_mock(uuid4(), 2, 0, ())
    with pytest.raises(RegistryConflict, match="reused"):
        reg.reconcile_mock(gen, 1, 0, ())
    with pytest.raises(RegistryConflict, match="reused"):
        reg.reconcile_mock(gen, 1, 1, (link,))


def test_concurrent_same_base_only_one_generation_wins():
    reg, _ = registry()
    generations = [uuid4(), uuid4()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(reg.reconcile_mock, gen, 1, 0, ()) for gen in generations]
    assert sum(attempt.exception() is None for attempt in attempts) == 1
    assert sum(isinstance(attempt.exception(), RegistryConflict) for attempt in attempts) == 1


def test_incomplete_inventory_and_claim_validation_fail_closed():
    reg, inventory = registry()
    inventory.mark_incomplete()
    with pytest.raises(RegistryConflict, match="incomplete"):
        reg.reconcile_mock(uuid4(), 1, 0, ())
    with pytest.raises(ValueError, match="numeric"):
        ResourceClaim(ClaimKind.ROUTING_TABLE_ID, "00100")
    with pytest.raises(ValueError, match="only typed TEMPEST"):
        replace(obj("tempest-link", ObjectKind.TUN_INTERFACE, ClaimKind.INTERFACE_NAME,
                    "tun0", uuid4(), 1), owner="other")
