"""Strict route snapshot comparison with a narrow IPv6 RA timer allowance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


class RouteSnapshotMismatch(ValueError):
    """Snapshots differ outside an allowed IPv6 RA lifetime countdown."""


@dataclass(frozen=True)
class TimerDrift:
    route: str
    before_expires: int
    after_expires: int
    elapsed_seconds: float


def validate_snapshot_id(value: str) -> str:
    """Reject empty values and common documentation placeholders."""
    placeholder = {
        "snapshot", "snapshot-id", "snapshot_id", "placeholder", "todo", "example",
        "verified", "verified-snapshot-id", "none", "null", "replace-with-real-snapshot-id",
    }
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", value)
            or value.lower() in placeholder
            or re.search(r"(?:^|[-_.])(?:placeholder|todo|example|change[-_.]?me|replace)(?:$|[-_.])", value, re.IGNORECASE)):
        raise RouteSnapshotMismatch("snapshot ID is empty, malformed, or an obvious placeholder")
    return value


def compare_route_snapshots(
    before_json: str,
    after_json: str,
    *,
    family: str,
    elapsed_seconds: float,
    timer_slack_seconds: float = 2.0,
) -> tuple[TimerDrift, ...]:
    """Require identical route records except a bounded IPv6 RA countdown.

    The complete route object is compared after removing ``expires``. A timer
    may only decrease, only for IPv6 routes whose protocol is ``ra``, and by
    no more than the measured capture interval plus the documented slack.
    """
    if family not in {"inet", "inet6"} or elapsed_seconds < 0 or timer_slack_seconds < 0:
        raise RouteSnapshotMismatch("invalid route comparison context")
    try:
        before = json.loads(before_json)
        after = json.loads(after_json)
    except (ValueError, TypeError) as exc:
        raise RouteSnapshotMismatch("invalid route JSON") from exc
    if not isinstance(before, list) or not isinstance(after, list):
        raise RouteSnapshotMismatch("route feeds must be arrays")
    if len(before) != len(after) or any(not isinstance(row, dict) for row in before + after):
        raise RouteSnapshotMismatch("route inventory changed")

    def normalized(row: dict) -> str:
        return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    # Non-RA routes and all IPv4 routes remain byte-semantically strict.
    drifts: list[TimerDrift] = []
    unmatched_after = list(after)
    for row in before:
        ra_timer = family == "inet6" and row.get("protocol") == "ra" and type(row.get("expires")) is int
        if ra_timer:
            stable = dict(row)
            old_expires = stable.pop("expires")
            key = normalized(stable)
            matches = [candidate for candidate in unmatched_after
                       if isinstance(candidate, dict)
                       and candidate.get("protocol") == "ra"
                       and type(candidate.get("expires")) is int
                       and normalized({k: v for k, v in candidate.items() if k != "expires"}) == key]
            if not matches:
                raise RouteSnapshotMismatch("IPv6 RA route properties changed")
            match = matches[0]
            new_expires = match["expires"]
            if old_expires < 0 or new_expires < 0:
                raise RouteSnapshotMismatch("invalid IPv6 RA expires value")
            drift = old_expires - new_expires
            if drift < 0 or drift > elapsed_seconds + timer_slack_seconds:
                raise RouteSnapshotMismatch("IPv6 RA expires drift outside elapsed-time allowance")
            unmatched_after.remove(match)
            if drift:
                drifts.append(TimerDrift(key, old_expires, new_expires, elapsed_seconds))
            continue
        key = normalized(row)
        match = next((candidate for candidate in unmatched_after
                      if isinstance(candidate, dict) and normalized(candidate) == key), None)
        if match is None:
            raise RouteSnapshotMismatch("route properties changed")
        unmatched_after.remove(match)
    if unmatched_after:
        raise RouteSnapshotMismatch("route inventory changed")
    return tuple(drifts)
