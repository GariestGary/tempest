"""Fixture-only Linux inventory checks; safe on Windows without privileges."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.network_agent.linux_inventory import (
    InventoryError, NamespaceIdentity, compare_agent_receipts, parse_linux_inventory,
    reject_unproven_desired,
)
from app.network_agent.registry import (
    ClaimKind, NetworkObject, ObjectKind, RegistryConflict, ResourceClaim,
)


FIXTURES = Path(__file__).parent / "fixtures" / "linux_inventory"
NAMESPACE = NamespaceIdentity("tempest-ns-lab", str(uuid4()), 4, 4026532999)


def feeds():
    return {f"{name}_json": (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
            for name in ("link", "rule", "route", "nft")}


def snapshot(data=None):
    data = data or feeds()
    return parse_linux_inventory(links_json=data["link_json"], rules_json=data["rule_json"],
                                 routes_json=data["route_json"], nft_json=data["nft_json"],
                                 namespace=NAMESPACE)


def intent(kind, claim, value):
    return NetworkObject("tempest-wanted", kind, NAMESPACE.name, "tempest", uuid4(), 1,
                         (ResourceClaim(claim, value),), "a" * 64)


def test_decodes_four_feeds_without_minting_ownership():
    result = snapshot()
    assert result.complete and len(result.objects) == 11
    assert all(obj.proof is None for obj in result.objects)
    assert {obj.kind for obj in result.objects} >= {ObjectKind.NFT_TABLE, ObjectKind.NFT_CHAIN,
                                                  ObjectKind.NFT_RULE, ObjectKind.ROUTE,
                                                  ObjectKind.IP_RULE, ObjectKind.WIREGUARD_INTERFACE}
    rule = next(obj for obj in result.objects if obj.kind == ObjectKind.IP_RULE
                and ResourceClaim(ClaimKind.IP_RULE_PRIORITY, "100") in obj.claims)
    assert ResourceClaim(ClaimKind.FWMARK, "42") in rule.claims
    link = next(obj for obj in result.objects if obj.kind == ObjectKind.WIREGUARD_INTERFACE)
    assert ResourceClaim(ClaimKind.INTERFACE_INDEX, "7") in link.claims


def test_supported_json_variants_are_canonicalized():
    data = feeds()
    rules = json.loads(data["rule_json"])
    rules[1]["pref"] = rules[1].pop("priority")
    rules[1]["table"] = "100"
    rules[1].pop("family")  # Source prefix supplies unambiguous family.
    data["rule_json"] = json.dumps(rules)
    routes = json.loads(data["route_json"])
    routes[0].pop("family")
    data["route_json"] = json.dumps(routes)
    nft = json.loads(data["nft_json"])
    nft["nftables"].pop(0)  # Some JSON output has no metainfo record.
    data["nft_json"] = json.dumps(nft)
    result = snapshot(data)
    assert result.complete and len(result.objects) == 11
    rule = next(o for o in result.objects if o.kind == ObjectKind.IP_RULE
                and ResourceClaim(ClaimKind.IP_RULE_PRIORITY, "100") in o.claims)
    assert json.loads(rule.kernel_key)[4] == 255  # fwmark mask is retained.


def test_foreign_nft_name_is_not_adopted():
    result = snapshot()
    table = next(obj for obj in result.objects if obj.kind == ObjectKind.NFT_TABLE)
    desired = intent(ObjectKind.NFT_TABLE, ClaimKind.NFT_TABLE_NAME, table.claims[0].value)
    with pytest.raises(RegistryConflict, match="unproven"):
        reject_unproven_desired(result, (desired,), namespace=NAMESPACE)
    assert table.proof is None


def test_nft_handle_reuse_does_not_make_owner():
    first = snapshot()
    data = feeds()
    nft = json.loads(data["nft_json"])
    nft["nftables"][-1]["rule"]["expr"] = [{"accept": None}]
    data["nft_json"] = json.dumps(nft)
    second = snapshot(data)
    old = next(obj for obj in first.objects if obj.kind == ObjectKind.NFT_RULE)
    new = next(obj for obj in second.objects if obj.kind == ObjectKind.NFT_RULE)
    assert old.object_id == new.object_id  # Handle is a selector within the chain.
    assert old.fingerprint != new.fingerprint and new.proof is None


@pytest.mark.parametrize("field,new_value", [("ifname", "someone-else"), ("ifindex", 9)])
def test_reused_interface_name_or_index_is_unproven(field, new_value):
    data = feeds()
    links = json.loads(data["link_json"])
    links[1][field] = new_value
    data["link_json"] = json.dumps(links)
    if field == "ifname":
        routes = json.loads(data["route_json"])
        routes[0]["dev"] = new_value
        data["route_json"] = json.dumps(routes)
    old = next(o for o in snapshot().objects if o.kind == ObjectKind.WIREGUARD_INTERFACE)
    new = next(o for o in snapshot(data).objects if o.kind == ObjectKind.WIREGUARD_INTERFACE)
    assert old.object_id != new.object_id and new.proof is None


def test_routing_table_and_rule_selector_conflicts():
    result = snapshot()
    table = intent(ObjectKind.ROUTING_TABLE, ClaimKind.ROUTING_TABLE_ID, "100")
    with pytest.raises(RegistryConflict, match="routing table"):
        reject_unproven_desired(result, (table,), namespace=NAMESPACE)
    observed_rule = next(o for o in result.objects if o.kind == ObjectKind.IP_RULE
                         and ResourceClaim(ClaimKind.IP_RULE_PRIORITY, "100") in o.claims)
    rule_match = next(c.value for c in observed_rule.claims if c.kind == ClaimKind.IP_RULE_MATCH)
    rule = NetworkObject("tempest-rule", ObjectKind.IP_RULE, NAMESPACE.name, "tempest", uuid4(), 1,
                         (ResourceClaim(ClaimKind.IP_RULE_PRIORITY, "100"),
                          ResourceClaim(ClaimKind.FWMARK, "43"),
                          ResourceClaim(ClaimKind.FWMARK_MASK, "255"),
                          ResourceClaim(ClaimKind.IP_RULE_MATCH, rule_match)), "a" * 64)
    with pytest.raises(RegistryConflict, match="unproven"):
        reject_unproven_desired(result, (rule,), namespace=NAMESPACE)


def test_rule_mark_mask_overlap_with_other_priority_is_a_conflict():
    result = snapshot()
    observed = next(o for o in result.objects if o.kind == ObjectKind.IP_RULE
                    and ResourceClaim(ClaimKind.IP_RULE_PRIORITY, "100") in o.claims)
    rule_match = next(c.value for c in observed.claims if c.kind == ClaimKind.IP_RULE_MATCH)
    wanted = NetworkObject("tempest-rule", ObjectKind.IP_RULE, NAMESPACE.name, "tempest", uuid4(), 1,
                           (ResourceClaim(ClaimKind.IP_RULE_PRIORITY, "101"),
                            ResourceClaim(ClaimKind.FWMARK, "298"),
                            ResourceClaim(ClaimKind.FWMARK_MASK, "511"),
                            ResourceClaim(ClaimKind.IP_RULE_MATCH, rule_match)), "a" * 64)
    with pytest.raises(RegistryConflict, match="selectors/mark overlap"):
        reject_unproven_desired(result, (wanted,), namespace=NAMESPACE)


@pytest.mark.parametrize("feed", ("link", "rule", "route", "nft"))
def test_bad_or_truncated_json_fails_closed(feed):
    data = feeds()
    data[f"{feed}_json"] = "{"
    with pytest.raises(InventoryError):
        snapshot(data)


@pytest.mark.parametrize("feed,field", [("link", "ifindex"), ("rule", "priority"),
                                        ("route", "dst")])
def test_missing_identity_field_fails_closed(feed, field):
    data = feeds()
    rows = json.loads(data[f"{feed}_json"])
    rows[0].pop(field)
    data[f"{feed}_json"] = json.dumps(rows)
    with pytest.raises(InventoryError):
        snapshot(data)


def test_null_destination_and_unhashable_table_fail_closed():
    data = feeds()
    routes = json.loads(data["route_json"])
    routes[0]["dst"] = None
    data["route_json"] = json.dumps(routes)
    with pytest.raises(InventoryError):
        snapshot(data)
    routes[0]["dst"] = "10.42.0.0/24"
    routes[0]["table"] = []
    data["route_json"] = json.dumps(routes)
    with pytest.raises(InventoryError):
        snapshot(data)


@pytest.mark.parametrize("feed,field", [("link", "peer_netnsid"), ("rule", "new_selector"),
                                        ("route", "new_metric"), ("nft", "set")])
def test_unknown_identity_affecting_field_or_entity_fails_closed(feed, field):
    data = feeds()
    rows = json.loads(data[f"{feed}_json"])
    if feed == "nft":
        rows["nftables"].append({field: {"family": "inet", "table": "tempest-filter", "name": "x"}})
    else:
        rows[0][field] = 1
    data[f"{feed}_json"] = json.dumps(rows)
    with pytest.raises(InventoryError):
        snapshot(data)


def test_namespace_identity_changes_observation_id():
    data = feeds()
    replaced = NamespaceIdentity(NAMESPACE.name, NAMESPACE.boot_id, NAMESPACE.netns_device, NAMESPACE.netns_inode + 1)
    other = parse_linux_inventory(links_json=data["link_json"], rules_json=data["rule_json"],
                                  routes_json=data["route_json"], nft_json=data["nft_json"],
                                  namespace=replaced)
    assert {o.object_id for o in snapshot().objects}.isdisjoint(o.object_id for o in other.objects)
    with pytest.raises(InventoryError, match="namespace identity changed"):
        reject_unproven_desired(other, (), namespace=NAMESPACE)


def test_diff_to_future_apply_requires_new_inventory_and_receipt():
    before = snapshot()
    data = feeds()
    nft = json.loads(data["nft_json"])
    nft["nftables"][-1]["rule"]["handle"] = 13
    data["nft_json"] = json.dumps(nft)
    after = snapshot(data)
    assert before.revision != after.revision
    assert all(o.proof is None for o in after.objects)


def test_restart_does_not_promote_matching_observation_to_receipt():
    before = snapshot()
    after = snapshot()
    assert before.revision == after.revision
    assert before.objects == after.objects
    assert all(o.proof is None for o in after.objects)
    link = next(o for o in after.objects if o.kind == ObjectKind.WIREGUARD_INTERFACE)
    desired = intent(ObjectKind.WIREGUARD_INTERFACE, ClaimKind.INTERFACE_NAME, "tempest-wg0")
    with pytest.raises(RegistryConflict, match="unproven"):
        reject_unproven_desired(after, (desired,), namespace=NAMESPACE)
    assert link.fingerprint
    with pytest.raises(RegistryConflict, match="cannot be verified"):
        compare_agent_receipts(after, (link,))


def test_route_slot_conflict_even_with_other_metric():
    result = snapshot()
    route = next(o for o in result.objects if o.kind == ObjectKind.ROUTE
                 and json.loads(o.claims[0].value)[1] == 100)
    key = json.loads(route.claims[0].value)
    key[5] += 1
    wanted = intent(ObjectKind.ROUTE, ClaimKind.ROUTE_KEY,
                    json.dumps(key, sort_keys=True, separators=(",", ":")))
    with pytest.raises(RegistryConflict, match="route destination/table"):
        reject_unproven_desired(result, (wanted,), namespace=NAMESPACE)


def test_unsupported_multipath_route_fails_closed():
    data = feeds()
    routes = json.loads(data["route_json"])
    routes[0]["multipath"] = [{"dev": "tempest-wg0", "weight": 1}]
    data["route_json"] = json.dumps(routes)
    with pytest.raises(InventoryError, match="unsupported"):
        snapshot(data)


def test_explicit_family_contradicting_prefix_fails_closed():
    data = feeds()
    routes = json.loads(data["route_json"])
    routes[0]["family"] = "inet6"
    data["route_json"] = json.dumps(routes)
    with pytest.raises(InventoryError, match="contradictory"):
        snapshot(data)


def test_missing_nft_parent_fails_closed():
    data = feeds()
    nft = json.loads(data["nft_json"])
    nft["nftables"].pop(1)
    data["nft_json"] = json.dumps(nft)
    with pytest.raises(InventoryError, match="parent table"):
        snapshot(data)


def test_duplicate_json_field_fails_closed():
    data = feeds()
    data["link_json"] = '[{"ifindex":1,"ifindex":2,"ifname":"lo"}]'
    with pytest.raises(InventoryError, match="duplicate JSON"):
        snapshot(data)
