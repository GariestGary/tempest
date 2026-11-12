"""Strict, read-only decoding of four Linux JSON inventory feeds.

No command runner lives here. The caller must capture all four feeds from the
same pre-provisioned namespace and supply its independently checked identity.
This parser never turns a kernel observation into an ownership receipt.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .registry import (
    ClaimKind, InventorySnapshot, NetworkObject, ObjectKind, ObservedObject,
    RegistryConflict, ResourceClaim,
)


class InventoryError(RegistryConflict):
    """The provided feed cannot support a safe, complete inventory."""


@dataclass(frozen=True)
class NamespaceIdentity:
    """Values read by a future trusted collector, never from NetworkPlan."""

    name: str
    boot_id: str
    netns_device: int
    netns_inode: int

    def __post_init__(self) -> None:
        try:
            UUID(self.boot_id)
        except (TypeError, ValueError) as exc:
            raise InventoryError("unverified boot identity") from exc
        if (not isinstance(self.name, str) or not self.name.startswith("tempest-ns-")
                or type(self.netns_device) is not int or self.netns_device <= 0
                or type(self.netns_inode) is not int or self.netns_inode <= 0):
            raise InventoryError("unverified namespace identity")


class InventoryReader(Protocol):
    def snapshot(self) -> InventorySnapshot: ...


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise InventoryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode(raw: str, feed: str) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, TypeError) as exc:
        raise InventoryError(f"invalid {feed} JSON") from exc


def _record(value: object, allowed: set[str], required: set[str], feed: str) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - allowed:
        raise InventoryError(f"incomplete or unsupported {feed} record")
    return value


def _number(value: object, feed: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 2**32 - 1:
        raise InventoryError(f"invalid {feed} number")
    return value


def _string(value: object, feed: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InventoryError(f"invalid {feed} string")
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _observed(kind: ObjectKind, namespace: NamespaceIdentity, key: object, claims: tuple[ResourceClaim, ...], data: object) -> ObservedObject:
    key_json = _canonical([kind.value, namespace.boot_id, namespace.netns_device, namespace.netns_inode, key])
    digest = hashlib.sha256(key_json.encode()).hexdigest()
    fingerprint = hashlib.sha256(_canonical(data).encode()).hexdigest()
    return ObservedObject(f"kernel-{digest}", kind, namespace.name, claims, fingerprint, None,
                          _canonical(key))


_LINK_FIELDS = {"ifindex", "ifname", "flags", "mtu", "qdisc", "operstate", "linkmode", "group", "txqlen", "link_type", "address", "broadcast", "linkinfo", "master", "link", "link_index", "link_netnsid", "promiscuity", "min_mtu", "max_mtu", "num_tx_queues", "num_rx_queues", "gso_max_size", "gso_max_segs", "gro_max_size"}
_RULE_FIELDS = {"priority", "pref", "family", "src", "dst", "from", "to", "fwmark", "fwmask", "table", "action", "protocol", "iif", "oif", "tos", "dsfield", "uidrange", "ipproto", "sport", "dport", "tun_id", "l3mdev", "suppress_prefixlength", "suppress_ifgroup", "goto", "realms", "invert"}
_ROUTE_FIELDS = {"dst", "src", "prefsrc", "table", "gateway", "dev", "protocol", "scope", "type", "metric", "priority", "tos", "pref", "flags", "family", "expires", "mtu", "advmss", "rtt", "rttvar", "window", "cwnd", "ssthresh", "rto_min", "initcwnd", "initrwnd", "quickack", "congctl", "ttl_propagate", "features"}
_NFT_FAMILIES = {"ip", "ip6", "inet", "arp", "bridge", "netdev"}


def _prefix(value: object, family: str, feed: str) -> str:
    if value in ("default", "all"):
        return "0.0.0.0/0" if family == "inet" else "::/0"
    try:
        network = ipaddress.ip_network(_string(value, feed), strict=False)
    except ValueError as exc:
        raise InventoryError(f"invalid {feed} prefix") from exc
    if network.version != (4 if family == "inet" else 6):
        raise InventoryError(f"wrong {feed} address family")
    return str(network)


def _family(row: dict, feed: str) -> str:
    value = row.get("family")
    if value is not None and value not in ("inet", "inet6"):
        raise InventoryError(f"unsupported {feed} family")
    # iproute2 sometimes omits family. Explicit address evidence is required.
    addresses = [row.get(k) for k in ("dst", "src", "from", "to", "gateway") if row.get(k) not in (None, "default", "all")]
    if not addresses and value is None:
        raise InventoryError(f"ambiguous {feed} family")
    versions = set()
    for address in addresses:
        try:
            versions.add(ipaddress.ip_network(_string(address, feed), strict=False).version)
        except ValueError as exc:
            raise InventoryError(f"invalid {feed} address") from exc
    if len(versions) != 1:
        if versions:
            raise InventoryError(f"contradictory {feed} family")
        return value
    inferred = "inet" if versions == {4} else "inet6"
    if value is not None and value != inferred:
        raise InventoryError(f"contradictory {feed} family")
    return inferred


def _table(value: object, feed: str) -> int:
    names = {"local": 255, "main": 254, "default": 253}
    if isinstance(value, str) and value in names:
        return names[value]
    if type(value) is int:
        return _number(value, feed, minimum=1)
    if isinstance(value, str) and value.isdecimal() and str(int(value)) == value:
        return _number(int(value), feed, minimum=1)
    raise InventoryError(f"unsupported {feed} table")


def parse_linux_inventory(*, links_json: str, rules_json: str, routes_json: str, nft_json: str, namespace: NamespaceIdentity) -> InventorySnapshot:
    """Decode captured JSON; complete means every supplied record was understood.

    This is not a claim that four separate kernel reads were atomic. A future
    live reader must detect changes across collection and before any apply.
    """
    links = _decode(links_json, "link")
    rules = _decode(rules_json, "rule")
    routes = _decode(routes_json, "route")
    nft = _decode(nft_json, "nft")
    if not all(isinstance(part, list) for part in (links, rules, routes)):
        raise InventoryError("incomplete ip inventory")
    if not isinstance(nft, dict) or set(nft) != {"nftables"} or not isinstance(nft["nftables"], list):
        raise InventoryError("incomplete nft inventory")
    objects: list[ObservedObject] = []
    link_names: set[str] = set()
    link_indexes: set[int] = set()
    for item in links:
        row = _record(item, _LINK_FIELDS, {"ifindex", "ifname"}, "link")
        name = _string(row["ifname"], "ifname")
        index = _number(row["ifindex"], "ifindex", minimum=1)
        if name in link_names or index in link_indexes:
            raise InventoryError("duplicate interface identity")
        link_names.add(name)
        link_indexes.add(index)
        linkinfo = row.get("linkinfo", {})
        if not isinstance(linkinfo, dict) or set(linkinfo) - {"info_kind", "info_data", "info_slave_kind", "info_slave_data"}:
            raise InventoryError("unsupported linkinfo")
        kind = linkinfo.get("info_kind")
        object_kind = ObjectKind.WIREGUARD_INTERFACE if kind == "wireguard" else ObjectKind.TUN_INTERFACE if kind == "tun" else ObjectKind.MANAGED_INTERFACE
        claim = ResourceClaim(ClaimKind.INTERFACE_NAME, name)
        objects.append(_observed(object_kind, namespace, [name, index],
                                 (claim, ResourceClaim(ClaimKind.INTERFACE_INDEX, str(index))), row))
    if "lo" not in link_names:
        raise InventoryError("loopback missing from link inventory")
    for item in rules:
        row = _record(item, _RULE_FIELDS, set(), "ip rule")
        family = _family(row, "ip rule")
        priority = _number(row.get("priority", row.get("pref")), "rule priority")
        if "priority" in row and "pref" in row and row["priority"] != row["pref"]:
            raise InventoryError("contradictory rule priority")
        mark = row.get("fwmark", 0)
        mask = row.get("fwmask", 2**32 - 1)
        if isinstance(mark, str):
            try:
                mark = int(mark, 0)
            except ValueError as exc:
                raise InventoryError("invalid fwmark") from exc
        if isinstance(mask, str):
            try:
                mask = int(mask, 0)
            except ValueError as exc:
                raise InventoryError("invalid fwmask") from exc
        mark = _number(mark, "fwmark")
        mask = _number(mask, "fwmask")
        if mask == 0 or mark & ~mask:
            raise InventoryError("ambiguous fwmark/mask")
        if "table" not in row and row.get("action", "to_tbl") == "to_tbl":
            raise InventoryError("rule lookup table missing")
        selectors = {k: row[k] for k in sorted(_RULE_FIELDS - {"priority", "pref", "family", "fwmark", "fwmask", "table"}) if k in row}
        for key in ("src", "dst", "from", "to"):
            if key in selectors:
                selectors[key] = _prefix(selectors[key], family, "rule")
        table = _table(row["table"], "rule") if "table" in row else None
        key = [family, priority, selectors, mark, mask, table]
        claims = (ResourceClaim(ClaimKind.IP_RULE_PRIORITY, str(priority)),
                  ResourceClaim(ClaimKind.FWMARK, str(mark)),
                  ResourceClaim(ClaimKind.FWMARK_MASK, str(mask)),
                  ResourceClaim(ClaimKind.IP_RULE_MATCH, _canonical([family, selectors, table])))
        objects.append(_observed(ObjectKind.IP_RULE, namespace, key, claims, row))
    for item in routes:
        row = _record(item, _ROUTE_FIELDS, {"dst"}, "route")
        family = _family(row, "route")
        if "dev" in row and row["dev"] not in link_names:
            raise InventoryError("route references absent interface")
        table = _table(row.get("table", "main"), "route")
        destination = _prefix(row["dst"], family, "route")
        source = _prefix(row["src"], family, "route source") if "src" in row else None
        tos = _number(row.get("tos", 0), "route tos")
        metric = _number(row.get("metric", row.get("priority", 0)), "route metric")
        if "metric" in row and "priority" in row and row["metric"] != row["priority"]:
            raise InventoryError("contradictory route metric")
        key = [family, table, destination, source, tos, metric, row.get("type", "unicast"), row.get("scope", "global"), row.get("protocol", "boot"), row.get("pref")]
        claim = ResourceClaim(ClaimKind.ROUTE_KEY, _canonical(key))
        objects.append(_observed(ObjectKind.ROUTE, namespace, key, (claim,), row))
    seen_nft: set[tuple] = set()
    nft_tables: set[tuple[str, str]] = set()
    nft_chains: set[tuple[str, str, str]] = set()
    for item in nft["nftables"]:
        if not isinstance(item, dict) or len(item) != 1:
            raise InventoryError("unsupported nft record")
        entity, value = next(iter(item.items()))
        if entity == "metainfo":
            _record(value, {"json_schema_version", "version", "release_name"}, {"json_schema_version"}, "nft metainfo")
            if value["json_schema_version"] != 1:
                raise InventoryError("unsupported nft JSON schema")
            continue
        fields = {"table": {"family", "name", "handle"}, "chain": {"family", "table", "name", "handle", "type", "hook", "prio", "policy", "dev"}, "rule": {"family", "table", "chain", "handle", "expr", "comment", "index"}}.get(entity)
        required = {"family", "name"} if entity == "table" else {"family", "table", "name"} if entity == "chain" else {"family", "table", "chain", "handle", "expr"}
        if fields is None:
            raise InventoryError(f"unsupported nft entity: {entity}")
        row = _record(value, fields, required, f"nft {entity}")
        family = _string(row["family"], "nft family")
        if family not in _NFT_FAMILIES:
            raise InventoryError("unsupported nft family")
        table_name = _string(row["name"], "nft table") if entity == "table" else _string(row["table"], "nft table")
        chain_name = _string(row["name"], "nft chain") if entity == "chain" else _string(row["chain"], "nft chain") if entity == "rule" else None
        handle = _number(row["handle"], "nft handle", minimum=1) if "handle" in row else None
        key = (family, table_name, chain_name, handle if entity == "rule" else None)
        if entity == "table":
            nft_tables.add((family, table_name))
        elif entity == "chain":
            nft_chains.add((family, table_name, chain_name))
        if key in seen_nft:
            raise InventoryError("duplicate nft identity")
        seen_nft.add(key)
        if entity == "rule" and not isinstance(row["expr"], list):
            raise InventoryError("invalid nft rule expression")
        kind = {"table": ObjectKind.NFT_TABLE, "chain": ObjectKind.NFT_CHAIN, "rule": ObjectKind.NFT_RULE}[entity]
        claim_kind = {"table": ClaimKind.NFT_TABLE_NAME, "chain": ClaimKind.NFT_CHAIN_KEY, "rule": ClaimKind.NFT_RULE_KEY}[entity]
        claim = ResourceClaim(claim_kind, _canonical(key))
        objects.append(_observed(kind, namespace, key, (claim,), row))
    ids = [obj.object_id for obj in objects]
    if len(ids) != len(set(ids)):
        raise InventoryError("duplicate observed object")
    for item in nft["nftables"]:
        if "chain" in item or "rule" in item:
            row = item.get("chain", item.get("rule"))
            if (row["family"], row["table"]) not in nft_tables:
                raise InventoryError("nft parent table missing")
            if "rule" in item and (row["family"], row["table"], row["chain"]) not in nft_chains:
                raise InventoryError("nft parent chain missing")
    # A digest is useful for read-only comparisons, not an atomic kernel CAS.
    revision = int.from_bytes(hashlib.sha256(_canonical(sorted((o.object_id, o.fingerprint) for o in objects)).encode()).digest()[:8], "big")
    return InventorySnapshot(revision, True, tuple(objects),
                             (namespace.boot_id, namespace.netns_device, namespace.netns_inode))


def reject_unproven_desired(
    snapshot: InventorySnapshot, desired: tuple[NetworkObject, ...], *, namespace: NamespaceIdentity,
) -> None:
    """Conservative conflict check; never adopts a discovered object."""
    if not snapshot.complete or snapshot.namespace_identity is None:
        raise InventoryError("inventory incomplete")
    if snapshot.namespace_identity != (namespace.boot_id, namespace.netns_device, namespace.netns_inode):
        raise InventoryError("namespace identity changed")
    for intent in desired:
        if intent.namespace_id != namespace.name:
            raise InventoryError("requested object escaped target namespace")
        wanted_nft = None
        wanted_route = None
        wanted_rule = None
        if intent.kind in {ObjectKind.NFT_TABLE, ObjectKind.NFT_CHAIN, ObjectKind.NFT_RULE}:
            claim_kind = {ObjectKind.NFT_TABLE: ClaimKind.NFT_TABLE_NAME,
                          ObjectKind.NFT_CHAIN: ClaimKind.NFT_CHAIN_KEY,
                          ObjectKind.NFT_RULE: ClaimKind.NFT_RULE_KEY}[intent.kind]
            wanted_nft = _requested_key(next(c.value for c in intent.claims if c.kind == claim_kind), 4, "nft")
            if wanted_nft[0] not in _NFT_FAMILIES or not all(isinstance(x, str) and x for x in wanted_nft[:2]):
                raise InventoryError("invalid requested nft key")
            if intent.kind != ObjectKind.NFT_TABLE and not isinstance(wanted_nft[2], str):
                raise InventoryError("invalid requested nft chain")
        if intent.kind == ObjectKind.ROUTE:
            wanted_route = _requested_key(next(c.value for c in intent.claims if c.kind == ClaimKind.ROUTE_KEY), 10, "route")
        if intent.kind == ObjectKind.IP_RULE:
            claim_map = {c.kind: c.value for c in intent.claims}
            if not {ClaimKind.FWMARK_MASK, ClaimKind.IP_RULE_MATCH} <= claim_map.keys():
                raise InventoryError("requested rule lacks mark mask or selectors")
            match = _requested_key(claim_map[ClaimKind.IP_RULE_MATCH], 3, "ip rule")
            if match[0] not in {"inet", "inet6"} or not isinstance(match[1], dict):
                raise InventoryError("invalid requested rule selectors")
            wanted_rule = (match, int(claim_map[ClaimKind.FWMARK]), int(claim_map[ClaimKind.FWMARK_MASK]))
        for observed in snapshot.objects:
            if intent.namespace_id != observed.namespace_id:
                continue
            if any(claim in observed.claims for claim in intent.claims
                   if claim.kind not in {ClaimKind.FWMARK_MASK, ClaimKind.IP_RULE_MATCH}):
                raise RegistryConflict("desired resource collides with unproven observation")
            if wanted_rule is not None and observed.kind == ObjectKind.IP_RULE:
                seen_rule = json.loads(observed.kernel_key)
                match, mark, mask = wanted_rule
                common_mask = mask & seen_rule[4]
                if match[0] == seen_rule[0] and mark & common_mask == seen_rule[3] & common_mask:
                    raise RegistryConflict("ip rule selectors/mark overlap foreign rule")
            if wanted_nft is not None and observed.kind in {ObjectKind.NFT_TABLE, ObjectKind.NFT_CHAIN, ObjectKind.NFT_RULE}:
                seen_nft = json.loads(observed.kernel_key)
                if wanted_nft[:2] == seen_nft[:2] and (
                    intent.kind == ObjectKind.NFT_TABLE or observed.kind == ObjectKind.NFT_TABLE
                    or wanted_nft[2] == seen_nft[2]
                ):
                    raise RegistryConflict("nft parent or rule collides with unproven observation")
            if intent.kind == ObjectKind.ROUTE and observed.kind == ObjectKind.ROUTE:
                seen = json.loads(observed.kernel_key)
                if wanted_route[:3] == seen[:3]:
                    raise RegistryConflict("route destination/table slot contains foreign route")
            if intent.kind == ObjectKind.ROUTING_TABLE and observed.kind == ObjectKind.ROUTE:
                table_id = next(c.value for c in intent.claims if c.kind == ClaimKind.ROUTING_TABLE_ID)
                route_key = next(c.value for c in observed.claims if c.kind == ClaimKind.ROUTE_KEY)
                if json.loads(route_key)[1] == int(table_id):
                    raise RegistryConflict("routing table contains foreign route")
            if intent.kind == ObjectKind.ROUTING_TABLE and observed.kind == ObjectKind.IP_RULE:
                table_id = next(c.value for c in intent.claims if c.kind == ClaimKind.ROUTING_TABLE_ID)
                if json.loads(observed.kernel_key)[5] == int(table_id):
                    raise RegistryConflict("routing table referenced by foreign rule")


def _requested_key(value: str, length: int, feed: str) -> list:
    try:
        key = json.loads(value)
    except ValueError as exc:
        raise InventoryError(f"noncanonical requested {feed} key") from exc
    if not isinstance(key, list) or len(key) != length or _canonical(key) != value:
        raise InventoryError(f"noncanonical requested {feed} key")
    return key


def compare_agent_receipts(snapshot: InventorySnapshot, receipts: tuple[ObservedObject, ...]) -> None:
    """Fail closed until a trusted collector can bind protected receipts.

    A fixture observation has no creation continuity evidence. Equal selectors,
    fingerprint, namespace identity and an Agent journal entry still cannot
    distinguish a recycled kernel object after an Agent restart.
    """
    if not snapshot.complete or snapshot.namespace_identity is None:
        raise InventoryError("inventory incomplete")
    if receipts:
        raise RegistryConflict("protected receipt cannot be verified by fixture inventory")
