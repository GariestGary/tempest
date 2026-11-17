"""Fail-closed validators used only by the manual disposable-VM trial."""

from __future__ import annotations

import json


class TrialSafetyError(ValueError):
    """An operator trial selector or cleanup shape is ambiguous."""


def validate_trial_selectors(
    *,
    rules: object,
    routes: object,
    nftables: object,
    expected: str,
    nft_table: str,
    priority: int,
    mark: int,
    route_table: int,
    destination: str,
    route_metric: int,
) -> None:
    """Validate a live trial snapshot against its exact planned state.

    ``expected`` encodes the monotonic seed states in table/rule/route order:
    ``000``, ``100``, ``110`` or ``111``. Objects that belong to the trial are
    excluded only from the matching resource group; objects of another type
    are never compared to them.
    """
    if expected not in {"000", "100", "110", "111"}:
        raise TrialSafetyError("invalid trial state")
    if not isinstance(rules, list) or not isinstance(routes, list) or not isinstance(nftables, list):
        raise TrialSafetyError("incomplete selector inventory")
    if any(not isinstance(row, dict) for row in rules + routes):
        raise TrialSafetyError("ambiguous ip selector inventory")

    def table_id(row: dict) -> object:
        value = row.get("table")
        if isinstance(value, str):
            value = {"local": 255, "main": 254, "default": 253}.get(value, value)
        return int(value) if isinstance(value, str) and value.isdecimal() else value

    def mark_value(row: dict, key: str, default: int) -> int:
        try:
            return int(str(row.get(key, default)), 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TrialSafetyError("ambiguous ip rule mark selector") from exc

    wanted_table, wanted_rule, wanted_route = (bit == "1" for bit in expected)

    tables = []
    for entity in nftables:
        if not isinstance(entity, dict) or len(entity) != 1:
            raise TrialSafetyError("ambiguous nft selector inventory")
        kind, row = next(iter(entity.items()))
        if kind not in {"metainfo", "table", "chain", "rule", "set", "map", "element", "counter", "quota", "flowtable"}:
            raise TrialSafetyError("unknown nft selector entity")
        if not isinstance(row, dict):
            raise TrialSafetyError("ambiguous nft selector entity")
        own_table = kind == "table" and row.get("family") == "inet" and row.get("name") == nft_table
        if own_table:
            tables.append(row)
        elif kind != "metainfo" and row.get("family") == "inet" and (
            row.get("table") == nft_table or (kind == "table" and row.get("name") == nft_table)
        ):
            raise TrialSafetyError("nft table has a foreign child or colliding selector")
    if len(tables) != int(wanted_table):
        raise TrialSafetyError("nft table selector collision or unexpected state")

    priority_rows = [row for row in rules if _rule_priority(row) == priority]
    trial_rules = [
        row for row in priority_rows if _is_trial_rule(row, mark=mark, route_table=route_table,
                                                       table_id=table_id, mark_value=mark_value)
    ]
    overlapping_marks = [
        row for row in rules
        if mark & mark_value(row, "fwmask", 0xFFFFFFFF)
        == mark_value(row, "fwmark", 0) & mark_value(row, "fwmask", 0xFFFFFFFF)
    ]
    table_rules = [row for row in rules if table_id(row) == route_table]
    if any(row not in trial_rules for row in priority_rows):
        raise TrialSafetyError("ip rule priority already occupied")
    if any(row not in trial_rules for row in overlapping_marks):
        raise TrialSafetyError("fwmark overlaps an existing ip rule")
    # Compare ip-rule objects to the matching ip-rule objects. Comparing them
    # to route_trial was the bug: it rejected our own lookup rule during 110.
    if any(row not in trial_rules for row in table_rules):
        raise TrialSafetyError("routing table ID is referenced by another ip rule")
    if len(trial_rules) > 1:
        raise TrialSafetyError("ambiguous duplicate ip rule selector")
    if len(trial_rules) != int(wanted_rule):
        raise TrialSafetyError("ip rule selector collision or unexpected state")

    route_slots = [row for row in routes if row.get("dst") == destination and table_id(row) == route_table]
    trial_routes = [
        row for row in route_slots if _is_trial_route(row, destination=destination,
                                                       route_table=route_table, route_metric=route_metric,
                                                       table_id=table_id)
    ]
    table_routes = [row for row in routes if table_id(row) == route_table]
    if any(row not in trial_routes for row in route_slots):
        raise TrialSafetyError("route destination/table slot is occupied by another route")
    if any(row not in trial_routes for row in table_routes):
        raise TrialSafetyError("routing table ID contains another route")
    if len(trial_routes) > 1:
        raise TrialSafetyError("ambiguous duplicate route selector")
    if len(trial_routes) != int(wanted_route):
        raise TrialSafetyError("route selector collision or unexpected state")


def _rule_priority(row: dict) -> int:
    try:
        return int(row.get("priority", row.get("pref", -1)))
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrialSafetyError("ambiguous ip rule priority") from exc


def _is_trial_rule(row: dict, *, mark: int, route_table: int, table_id, mark_value) -> bool:
    # The trial creates a plain IPv4 lookup rule with only priority, fwmark
    # and table selectors. Additional match fields mean the live rule no
    # longer matches the exact object the script seeded, so cleanup must stop.
    allowed = {"family", "_trial_family", "priority", "pref", "fwmark", "fwmask",
               "table", "action", "protocol", "src", "dst"}
    if set(row) - allowed:
        return False
    if row.get("_trial_family", row.get("family", "inet")) != "inet":
        return False
    if "family" in row and row["family"] != "inet":
        return False
    if "priority" in row and "pref" in row and row["priority"] != row["pref"]:
        return False
    if row.get("action", "to_tbl") != "to_tbl" or row.get("protocol") not in {None, "boot"}:
        return False
    if row.get("src", "all") not in {"all", "default"} or row.get("dst", "all") not in {"all", "default"}:
        return False
    return (mark_value(row, "fwmark", 0) == mark
            and mark_value(row, "fwmask", 0xFFFFFFFF) == 0xFFFFFFFF
            and table_id(row) == route_table)


def _route_metric(row: dict) -> int:
    try:
        return int(row.get("metric", row.get("priority", 0)))
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrialSafetyError("ambiguous route metric") from exc


def _is_trial_route(row: dict, *, destination: str, route_table: int, route_metric: int, table_id) -> bool:
    allowed = {"_trial_family", "family", "dst", "table", "type", "protocol", "metric", "priority", "scope", "flags"}
    if set(row) - allowed or row.get("flags", []) != []:
        return False
    if row.get("_trial_family", row.get("family", "inet")) != "inet":
        return False
    if "family" in row and row["family"] != "inet":
        return False
    if "metric" in row and "priority" in row and row["metric"] != row["priority"]:
        return False
    return (row.get("dst") == destination and table_id(row) == route_table
            and row.get("type") == "blackhole" and row.get("protocol") == "static"
            and _route_metric(row) == route_metric and row.get("scope", "global") == "global")


def require_empty_nft_table(raw_json: str, *, family: str, table_name: str) -> None:
    """Allow table deletion only when a fresh nft JSON read has no children."""
    try:
        document = json.loads(raw_json)
    except (TypeError, ValueError) as exc:
        raise TrialSafetyError("invalid nft table JSON") from exc
    rows = document.get("nftables") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise TrialSafetyError("incomplete nft table inventory")
    tables = []
    for row in rows:
        if not isinstance(row, dict) or len(row) != 1:
            raise TrialSafetyError("ambiguous nft table inventory")
        if set(row) == {"metainfo"}:
            continue
        if set(row) != {"table"} or not isinstance(row["table"], dict):
            raise TrialSafetyError("nft table contains child or unknown objects")
        tables.append(row["table"])
    if len(tables) != 1:
        raise TrialSafetyError("expected exactly one nft table")
    if tables[0].get("family") != family or tables[0].get("name") != table_name:
        raise TrialSafetyError("nft table identity changed")
