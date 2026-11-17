"""Fail-closed validators used only by the manual disposable-VM trial."""

from __future__ import annotations

import json


class TrialSafetyError(ValueError):
    """An operator trial selector or cleanup shape is ambiguous."""


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
