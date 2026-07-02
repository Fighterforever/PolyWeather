from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_tiny_live_audit.v1"
TINY_LIVE_PAYOUT_TEMPLATE_COLUMNS = [
    "audit_date_utc",
    "wallet_or_account_note",
    "order_id",
    "client_order_id",
    "market_slug",
    "token_id",
    "time_on_book_minutes",
    "reward_qualified_minutes",
    "expected_reward_low",
    "expected_reward_base",
    "expected_reward_high",
    "actual_reward_received",
    "payout_time_utc",
    "payout_source",
    "tx_hash_or_statement_ref",
    "actual_markout",
    "adverse_selection_notes",
    "operator_notes",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def build_weather_lp_tiny_live_audit_report(
    *,
    order_rows: Iterable[Dict[str, Any]],
    update_rows: Iterable[Dict[str, Any]] = (),
    cancellation_rows: Iterable[Dict[str, Any]] = (),
    payout_rows: Iterable[Dict[str, Any]] = (),
    generated_at: str | None = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    orders = [row for row in order_rows if isinstance(row, dict)]
    updates = [row for row in update_rows if isinstance(row, dict)]
    cancellations = [row for row in cancellation_rows if isinstance(row, dict)]
    payouts = [row for row in payout_rows if isinstance(row, dict)]
    placed = [row for row in orders if row.get("placed") or (row.get("placement_result") or {}).get("placed")]
    open_count = max(0, len(placed) - len(cancellations))
    filled_count = sum(1 for row in updates if row.get("filled") or row.get("fill_occurred"))
    reward_minutes = sum(_safe_float(row.get("reward_qualified_minutes")) or 0.0 for row in updates)
    time_on_book = sum(_safe_float(row.get("time_on_book_minutes")) or 0.0 for row in updates)
    expected_low = sum(_safe_float(row.get("expected_reward_low")) or 0.0 for row in orders)
    expected_base = sum(_safe_float(row.get("expected_reward_base") or row.get("reward_score_proxy")) or 0.0 for row in orders)
    expected_high = sum(_safe_float(row.get("expected_reward_high")) or 0.0 for row in orders)
    actual_reward = sum(_safe_float(row.get("actual_reward_received")) or 0.0 for row in payouts) if payouts else None
    markouts = [_safe_float(row.get("markout")) for row in updates]
    markout = sum(value or 0.0 for value in markouts if value is not None) if any(value is not None for value in markouts) else None
    if payouts and actual_reward is not None:
        payout_gap_reason = "none"
        payout_observed = True
    elif not placed:
        payout_gap_reason = "no_tiny_live_orders_placed"
        payout_observed = False
    else:
        payout_gap_reason = "manual_payout_audit_required"
        payout_observed = False
    reason_counts = Counter(str(row.get("reason") or row.get("cancellation_reason") or "unknown") for row in cancellations)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "order_count": len(orders),
        "placed_order_count": len(placed),
        "open_order_count": open_count,
        "filled_count": filled_count,
        "canceled_count": len(cancellations),
        "time_on_book_minutes": round(time_on_book, 8),
        "reward_qualified_minutes": round(reward_minutes, 8),
        "expected_reward_low": expected_low,
        "expected_reward_base": expected_base,
        "expected_reward_high": expected_high,
        "actual_reward_received": actual_reward,
        "payout_observed": payout_observed,
        "payout_gap_reason": payout_gap_reason,
        "actual_vs_expected_ratio": (actual_reward / expected_base) if actual_reward is not None and expected_base else None,
        "below_minimum_payout_possible": bool(placed and not payouts and expected_base < 100.0),
        "markout": markout,
        "adverse_selection_notes": [row.get("adverse_selection_notes") for row in updates if row.get("adverse_selection_notes")],
        "cancellation_reason_counts": [{"reason": key, "count": value} for key, value in sorted(reason_counts.items())],
        "manual_template_required": not payout_observed,
        "live_order_path": bool(placed),
    }


def write_tiny_live_payout_manual_template(path: str | Path, rows: Iterable[Dict[str, Any]], generated_at: str | None = None) -> int:
    generated_at = generated_at or utc_now_iso()
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TINY_LIVE_PAYOUT_TEMPLATE_COLUMNS)
        writer.writeheader()
        for row in materialized:
            writer.writerow(
                {
                    "audit_date_utc": generated_at,
                    "wallet_or_account_note": "",
                    "order_id": (row.get("placement_result") or {}).get("order_id") or row.get("order_id"),
                    "client_order_id": row.get("client_order_id"),
                    "market_slug": row.get("market_slug"),
                    "token_id": row.get("token_id"),
                    "time_on_book_minutes": "",
                    "reward_qualified_minutes": "",
                    "expected_reward_low": row.get("expected_reward_low"),
                    "expected_reward_base": row.get("expected_reward_base") or row.get("reward_score_proxy"),
                    "expected_reward_high": row.get("expected_reward_high"),
                    "actual_reward_received": "",
                    "payout_time_utc": "",
                    "payout_source": "",
                    "tx_hash_or_statement_ref": "",
                    "actual_markout": "",
                    "adverse_selection_notes": "",
                    "operator_notes": "",
                }
            )
    return len(materialized)


__all__ = [
    "SCHEMA_VERSION",
    "build_weather_lp_tiny_live_audit_report",
    "load_jsonl",
    "utc_now_iso",
    "write_json",
    "write_tiny_live_payout_manual_template",
]
