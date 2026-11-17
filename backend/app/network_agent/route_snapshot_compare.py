"""Strict route snapshot comparison with a narrow IPv6 RA timer allowance."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Literal


class RouteSnapshotMismatch(ValueError):
    """Snapshots cannot be matched with only IPv6 RA lifetime differences."""


@dataclass(frozen=True)
class TimerDrift:
    route: str
    before_expires: int
    after_expires: int
    elapsed_seconds: float
    classification: Literal["countdown", "refresh_compatible"]


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
    """Match complete route records, allowing only IPv6 RA timer changes.

    A decrease is bounded by the capture interval plus slack. An increase is
    consistent with a lifetime refresh, but snapshots cannot identify its cause.
    """
    if family not in {"inet", "inet6"} or not all(
        math.isfinite(seconds) and seconds >= 0 for seconds in (elapsed_seconds, timer_slack_seconds)
    ):
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

    def index_routes(rows: list[dict]) -> dict[str, int | None]:
        timers = {}
        for row in rows:
            stable = dict(row)
            expires = None
            if family == "inet6" and row.get("protocol") == "ra" and "expires" in row:
                expires = stable.pop("expires")
                if type(expires) is not int or expires < 0:
                    raise RouteSnapshotMismatch("invalid IPv6 RA expires value")
            key = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            if key in timers:
                raise RouteSnapshotMismatch("ambiguous route snapshot match")
            timers[key] = expires
        return timers

    before_timers = index_routes(before)
    after_timers = index_routes(after)
    if before_timers.keys() != after_timers.keys():
        raise RouteSnapshotMismatch("route properties changed")
    drifts: list[TimerDrift] = []
    for key, old_expires in before_timers.items():
        new_expires = after_timers[key]
        if old_expires == new_expires:
            continue
        if old_expires is None or new_expires is None:
            raise RouteSnapshotMismatch("IPv6 RA expires field appeared or disappeared")
        if old_expires - new_expires > elapsed_seconds + timer_slack_seconds:
            raise RouteSnapshotMismatch("IPv6 RA expires countdown outside elapsed-time allowance")
        classification = "countdown" if new_expires < old_expires else "refresh_compatible"
        drifts.append(TimerDrift(key, old_expires, new_expires, elapsed_seconds, classification))
    return tuple(drifts)
