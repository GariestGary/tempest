"""Non-mutating checks for the operator-only foreign ownership VM runner."""

import os
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.network_agent.lab_trial_safety import (
    TrialSafetyError, require_empty_nft_table, validate_trial_selectors,
)


SCRIPT = Path(__file__).parents[2] / "scripts" / "lab" / "foreign-ownership-trial.sh"
FIXTURES = Path(__file__).parent / "fixtures" / "linux_inventory"
TABLE = "tempest_foreign_a1b2c3d4"
PRIORITY = 32345
MARK = 0x7E123456
ROUTE_TABLE = 32123
DESTINATION = "198.18.0.0/15"
ROUTE_METRIC = 42420


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is required for shell runner checks")
def test_trial_script_dry_run_is_read_only():
    result = subprocess.run(["bash", str(SCRIPT), "--dry-run"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "no state-changing commands executed" in result.stdout
    assert "198.18.0.0/15" in result.stdout


@pytest.mark.parametrize("snapshot_id", ["", "<verified-snapshot-id>", "snapshot", "TODO", "example-backup"])
@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash is required for shell runner checks")
def test_trial_dry_run_rejects_supplied_empty_or_placeholder_snapshot(snapshot_id):
    env = dict(os.environ, TEMPEST_SNAPSHOT_ID=snapshot_id)
    result = subprocess.run(["bash", str(SCRIPT), "--dry-run"], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Snapshot ID" in result.stderr
    assert "no state-changing" not in result.stdout


def test_trial_cleanup_uses_exact_route_rule_then_empty_table_guard():
    source = SCRIPT.read_text(encoding="utf-8")
    route_delete = source.index('target ip -4 route del blackhole "$PREFIX" table "$ROUTE_TABLE"')
    rule_delete = source.index('target ip -4 rule del priority "$PRIORITY" fwmark "$MARK" lookup "$ROUTE_TABLE"')
    table_guard = source.index('ensure_empty_trial_table; target nft delete table inet "$TABLE"')
    assert route_delete < rule_delete < table_guard
    assert "nft flush" not in source and "ip route flush" not in source and "ip rule flush" not in source
    assert "unknown children" in source


def trial_state(*, table=False, rule=False, route=False):
    rules = json.loads((FIXTURES / "rule.json").read_text())
    routes = json.loads((FIXTURES / "route.json").read_text())
    nftables = json.loads((FIXTURES / "nft.json").read_text())["nftables"]
    if table:
        nftables.append({"table": {"family": "inet", "name": TABLE}})
    if rule:
        rules.append({"family": "inet", "priority": PRIORITY, "fwmark": hex(MARK),
                      "fwmask": "0xffffffff", "table": ROUTE_TABLE, "action": "to_tbl"})
    if route:
        routes.append({"family": "inet", "dst": DESTINATION, "table": ROUTE_TABLE,
                       "type": "blackhole", "protocol": "static", "metric": ROUTE_METRIC})
    return rules, routes, nftables


def check_state(expected, state):
    rules, routes, nftables = state
    validate_trial_selectors(rules=rules, routes=routes, nftables=nftables,
                             expected=expected, nft_table=TABLE, priority=PRIORITY,
                             mark=MARK, route_table=ROUTE_TABLE,
                             destination=DESTINATION, route_metric=ROUTE_METRIC)


@pytest.mark.parametrize("expected,state", [
    ("000", (False, False, False)),
    ("100", (True, False, False)),
    ("110", (True, True, False)),
    ("111", (True, True, True)),
])
def test_selector_validator_accepts_each_seed_state_from_linux_json_fixtures(expected, state):
    check_state(expected, trial_state(table=state[0], rule=state[1], route=state[2]))


def test_selector_validator_accepts_exact_reverse_cleanup_states_110_100_000():
    check_state("110", trial_state(table=True, rule=True))
    check_state("100", trial_state(table=True))
    check_state("000", trial_state())


@pytest.mark.parametrize("expected,state,reason", [
    ("110", {"table": True, "rule": True, "foreign_table_rule": True}, "routing table ID is referenced"),
    ("000", {"foreign_priority": True}, "priority already occupied"),
    ("000", {"overlapping_mark": True}, "fwmark overlaps"),
    ("110", {"table": True, "rule": True, "foreign_route": True}, "routing table ID contains"),
    ("110", {"table": True, "rule": True, "route_slot_collision": True}, "route destination/table slot is occupied"),
    ("100", {"table": True, "child": True}, "foreign child"),
    ("000", {"table_name_collision": True}, "nft table selector collision"),
    ("111", {"table": True, "rule": True, "route": True, "duplicate_rule": True}, "ambiguous duplicate ip rule"),
])
def test_selector_validator_rejects_foreign_or_ambiguous_objects(expected, state, reason):
    rules, routes, nftables = trial_state(table=state.get("table", False),
                                         rule=state.get("rule", False),
                                         route=state.get("route", False))
    if state.get("foreign_table_rule"):
        rules.append({"family": "inet", "priority": PRIORITY + 1, "fwmark": "0x1",
                      "fwmask": "0xffffffff", "table": ROUTE_TABLE, "action": "to_tbl"})
    if state.get("foreign_priority"):
        rules.append({"family": "inet6", "priority": PRIORITY, "fwmark": "0x11111111",
                      "fwmask": "0xffffffff", "table": 300, "action": "to_tbl"})
    if state.get("overlapping_mark"):
        rules.append({"family": "inet", "priority": PRIORITY + 2, "fwmark": hex(MARK & 0xFF),
                      "fwmask": "0xff", "table": 300, "action": "to_tbl"})
    if state.get("foreign_route"):
        routes.append({"family": "inet", "dst": "203.0.113.0/24", "table": ROUTE_TABLE,
                       "type": "blackhole", "protocol": "static", "metric": 7})
    if state.get("route_slot_collision"):
        routes.append({"family": "inet", "dst": DESTINATION, "table": ROUTE_TABLE,
                       "type": "unicast", "gateway": "192.0.2.99", "protocol": "static", "metric": 7})
    if state.get("child"):
        nftables.append({"chain": {"family": "inet", "table": TABLE, "name": "unexpected"}})
    if state.get("table_name_collision"):
        nftables.append({"table": {"family": "inet", "name": TABLE}})
    if state.get("duplicate_rule"):
        rules.append({"family": "inet", "priority": PRIORITY, "fwmark": hex(MARK),
                      "fwmask": "0xffffffff", "table": ROUTE_TABLE, "action": "to_tbl"})
    with pytest.raises(TrialSafetyError, match=reason):
        validate_trial_selectors(rules=rules, routes=routes, nftables=nftables,
                                 expected=expected, nft_table=TABLE, priority=PRIORITY,
                                 mark=MARK, route_table=ROUTE_TABLE,
                                 destination=DESTINATION, route_metric=ROUTE_METRIC)


@pytest.mark.parametrize("rows", [
    [{"table": {"family": "inet", "name": "tempest_foreign_test"}},
     {"chain": {"family": "inet", "table": "tempest_foreign_test", "name": "unexpected"}}],
    [{"table": {"family": "inet", "name": "other"}}],
    [{"table": {"family": "inet", "name": "tempest_foreign_test"}},
     {"table": {"family": "inet", "name": "tempest_foreign_test"}}],
    [{"table": {"family": "inet", "name": "tempest_foreign_test"}}, {"set": {"name": "unknown"}}],
])
def test_cleanup_guard_rejects_unknown_or_changed_nft_table(rows):
    import json

    with pytest.raises(TrialSafetyError):
        require_empty_nft_table(json.dumps({"nftables": rows}), family="inet",
                                table_name="tempest_foreign_test")


def test_cleanup_guard_accepts_only_expected_empty_nft_table_and_metainfo():
    import json

    require_empty_nft_table(json.dumps({"nftables": [
        {"metainfo": {"json_schema_version": 1}},
        {"table": {"family": "inet", "name": "tempest_foreign_test"}},
    ]}), family="inet", table_name="tempest_foreign_test")
