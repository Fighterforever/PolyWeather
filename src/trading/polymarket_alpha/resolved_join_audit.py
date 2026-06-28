from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import build_resolved_outcome_lookup, write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_resolved_join_audit.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _market_indexes(markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    by_slug: Dict[str, Dict[str, Any]] = {}
    by_market_id: Dict[str, Dict[str, Any]] = {}
    by_condition_id: Dict[str, Dict[str, Any]] = {}
    by_token: Dict[str, Dict[str, Any]] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        if market.get("market_slug"):
            by_slug[_text(market.get("market_slug"))] = market
        if market.get("market_id"):
            by_market_id[_text(market.get("market_id"))] = market
        if market.get("condition_id"):
            by_condition_id[_text(market.get("condition_id"))] = market
        for token in market.get("token_ids") or []:
            by_token[_text(token)] = market
    return {
        "by_slug": by_slug,
        "by_market_id": by_market_id,
        "by_condition_id": by_condition_id,
        "by_token": by_token,
    }


def _find_market(row: Dict[str, Any], indexes: Dict[str, Dict[str, Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    for field, index_name in (
        ("market_slug", "by_slug"),
        ("market_id", "by_market_id"),
        ("condition_id", "by_condition_id"),
        ("token_id", "by_token"),
    ):
        value = _text(row.get(field))
        if value and value in indexes[index_name]:
            return indexes[index_name][value]
    return None


def _terminal_payouts(market: Dict[str, Any]) -> List[Optional[float]]:
    payouts: List[Optional[float]] = []
    for value in market.get("outcome_prices") or []:
        try:
            price = float(value)
        except (TypeError, ValueError):
            payouts.append(None)
            continue
        if price >= 0.999:
            payouts.append(1.0)
        elif price <= 0.001:
            payouts.append(0.0)
        else:
            payouts.append(None)
    return payouts


def classify_missing_outcome(row: Dict[str, Any], market: Optional[Dict[str, Any]]) -> str:
    if market is None:
        return "missing_closed_market_record"
    if market.get("active") or not market.get("closed"):
        return "active_or_unresolved"
    payouts = _terminal_payouts(market)
    if not market.get("resolved") and not any(value is not None for value in payouts):
        return "closed_but_no_resolution"
    token = _text(row.get("token_id"))
    tokens = [_text(value) for value in market.get("token_ids") or []]
    if token and token not in tokens:
        return "token_id_mismatch"
    if row.get("condition_id") and market.get("condition_id") and _text(row.get("condition_id")) != _text(market.get("condition_id")):
        return "condition_id_mismatch"
    outcomes = [_text(value).lower() for value in market.get("outcomes") or []]
    label = _text(row.get("outcome_label")).lower()
    if label and outcomes and label not in outcomes:
        return "outcome_label_mismatch"
    if not any(value is not None for value in payouts):
        return "missing_payout"
    return "missing_payout"


def build_resolved_join_audit_report(
    *,
    snapshot_rows: Iterable[Dict[str, Any]],
    market_rows: Iterable[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshots = [row for row in snapshot_rows if isinstance(row, dict)]
    markets = [row for row in market_rows if isinstance(row, dict)]
    indexes = _market_indexes(markets)
    lookup = build_resolved_outcome_lookup(markets)
    missing_rows = [row for row in snapshots if row.get("resolved_payout") is None]
    reason_counts: Counter[str] = Counter()
    samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    repair_attempted = 0
    repair_success = 0
    for row in missing_rows:
        market = _find_market(row, indexes)
        reason = classify_missing_outcome(row, market)
        reason_counts[reason] += 1
        if len(samples[reason]) < 5:
            samples[reason].append(
                {
                    "market_slug": row.get("market_slug"),
                    "market_id": row.get("market_id"),
                    "condition_id": row.get("condition_id"),
                    "token_id": row.get("token_id"),
                    "outcome_label": row.get("outcome_label"),
                    "category": row.get("category"),
                    "decision_time": row.get("decision_time") or row.get("timestamp"),
                    "gap_reason": reason,
                }
            )
        if reason in {"token_id_mismatch", "condition_id_mismatch", "outcome_label_mismatch", "missing_payout"}:
            repair_attempted += 1
            token_match = lookup.get("by_token", {}).get(_text(row.get("token_id")))
            if token_match and token_match.get("resolved_payout") is not None:
                repair_success += 1
    unique_families = {row.get("event_family_id") for row in snapshots if row.get("event_family_id")}
    unique_resolved_families = {row.get("event_family_id") for row in snapshots if row.get("resolved_payout") is not None and row.get("event_family_id")}
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "snapshot_row_count": len(snapshots),
        "missing_outcome_count": len(missing_rows),
        "resolved_snapshot_count": len(snapshots) - len(missing_rows),
        "unique_event_family_count": len(unique_families),
        "unique_resolved_event_family_count": len(unique_resolved_families),
        "missing_outcome_by_market_status": [
            {"reason": key, "count": value}
            for key, value in sorted(reason_counts.items())
        ],
        "sample_rows_by_reason": dict(samples),
        "repair_attempted_count": int((manifest or {}).get("repair_attempted_count") or repair_attempted),
        "repair_success_count": int((manifest or {}).get("repair_success_count") or repair_success),
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


__all__ = [
    "SCHEMA_VERSION",
    "build_resolved_join_audit_report",
    "classify_missing_outcome",
    "load_json",
    "load_jsonl",
    "write_json",
]
