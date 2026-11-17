"""Non-mutating checks for the operator-only foreign ownership VM runner."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.network_agent.lab_trial_safety import TrialSafetyError, require_empty_nft_table


SCRIPT = Path(__file__).parents[2] / "scripts" / "lab" / "foreign-ownership-trial.sh"


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


def test_selector_preflight_checks_priority_mark_table_and_route_slot():
    source = SCRIPT.read_text(encoding="utf-8")
    for fail_closed_reason in (
        "ip rule priority already occupied",
        "fwmark overlaps an existing ip rule",
        "routing table ID is referenced by another ip rule",
        "routing table ID contains another route",
        "route destination/table slot is occupied by another route",
    ):
        assert fail_closed_reason in source


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
