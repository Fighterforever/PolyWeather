from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_probability_edge_journal.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _parse_utc(value: Any) -> Optional[datetime]:
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


def _side_price(side: str, yes_price: float) -> float:
    return yes_price if str(side).upper() == "YES" else 1.0 - yes_price


def _by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "")
        if token_id:
            grouped[token_id].append(row)
    for members in grouped.values():
        members.sort(key=lambda row: str(row.get("timestamp") or ""))
    return grouped


def _first_after(rows: List[Dict[str, Any]], at: datetime, horizon_seconds: int) -> Optional[Dict[str, Any]]:
    target = at.timestamp() + int(horizon_seconds)
    for row in rows:
        ts = _parse_utc(row.get("timestamp"))
        if ts is None:
            continue
        if ts.timestamp() >= target:
            return row
    return None


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 8) if values else None


def _bucket_summary(rows: List[Dict[str, Any]], key: str, value_key: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        value = _safe_float(row.get(value_key))
        if value is None:
            continue
        grouped[str(row.get(key) or "missing")].append(value)
    return [
        {"bucket": bucket, "sample_count": len(values), "mean": _mean(values)}
        for bucket, values in sorted(grouped.items())
    ]


def build_markout_report(
    *,
    fills: Iterable[Dict[str, Any]],
    price_rows: Iterable[Dict[str, Any]],
    horizons: Tuple[int, ...] = (60, 300, 900, 3600),
) -> Dict[str, Any]:
    materialized_fills = [row for row in fills if isinstance(row, dict)]
    histories = _by_token(price_rows)
    markouts: List[Dict[str, Any]] = []
    for fill in materialized_fills:
        token_id = str(fill.get("token_id") or "")
        entry_time = _parse_utc(fill.get("timestamp") or fill.get("generated_at"))
        q_effective = _safe_float(fill.get("q_effective"))
        if not token_id or entry_time is None or q_effective is None:
            continue
        token_history = histories.get(token_id) or []
        for horizon in horizons:
            future = _first_after(token_history, entry_time, horizon)
            if future is None:
                continue
            yes_price = _safe_float(future.get("price_mid") or future.get("price"))
            if yes_price is None:
                continue
            future_side_price = _side_price(str(fill.get("side") or "YES"), yes_price)
            markouts.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.markout",
                    "market_slug": fill.get("market_slug"),
                    "token_id": token_id,
                    "category": fill.get("category"),
                    "side": fill.get("side"),
                    "model_source": fill.get("model_source"),
                    "entry_time": fill.get("timestamp") or fill.get("generated_at"),
                    "future_time": future.get("timestamp"),
                    "horizon_seconds": horizon,
                    "q_effective": q_effective,
                    "future_side_price": round(future_side_price, 8),
                    "markout": round(future_side_price - q_effective, 8),
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    one_hour = [row for row in markouts if int(row.get("horizon_seconds") or 0) == 3600]
    all_values = [_safe_float(row.get("markout")) for row in markouts]
    all_values = [value for value in all_values if value is not None]
    one_hour_values = [_safe_float(row.get("markout")) for row in one_hour]
    one_hour_values = [value for value in one_hour_values if value is not None]
    report = {
        "schema_version": f"{SCHEMA_VERSION}.markout_report",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_count": len(materialized_fills),
        "markout_count": len(markouts),
        "mean_markout": _mean(all_values),
        "mean_markout_1h": _mean(one_hour_values),
        "markout_status": (
            "ready_waiting_for_probability_edge_fills"
            if not materialized_fills
            else "no_forward_markout_yet"
            if not markouts
            else "forward_markout_positive"
            if (_mean(one_hour_values) or _mean(all_values) or 0.0) > 0
            else "forward_markout_nonpositive"
        ),
        "by_category": _bucket_summary(markouts, "category", "markout"),
        "by_model_source": _bucket_summary(markouts, "model_source", "markout"),
        "markouts": markouts,
    }
    return report


def _resolved_payout_by_token(dataset_rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    payouts: Dict[str, float] = {}
    for row in dataset_rows:
        if not isinstance(row, dict):
            continue
        payout = _safe_float(row.get("resolved_payout"))
        token_id = str(row.get("token_id") or "")
        if token_id and payout is not None:
            payouts[token_id] = payout
    return payouts


def build_resolved_audit_report(
    *,
    fills: Iterable[Dict[str, Any]],
    dataset_rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    materialized_fills = [row for row in fills if isinstance(row, dict)]
    payouts = _resolved_payout_by_token(dataset_rows)
    audits: List[Dict[str, Any]] = []
    pnl_values: List[float] = []
    for fill in materialized_fills:
        token_id = str(fill.get("token_id") or "")
        payout = payouts.get(token_id)
        q_effective = _safe_float(fill.get("q_effective"))
        resolved_pnl_cents = None
        if payout is not None and q_effective is not None:
            side_payout = payout if str(fill.get("side") or "YES").upper() == "YES" else 1.0 - payout
            resolved_pnl_cents = round(100.0 * (side_payout - q_effective), 6)
            pnl_values.append(resolved_pnl_cents)
        audits.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.resolved_audit",
                "market_slug": fill.get("market_slug"),
                "token_id": token_id,
                "category": fill.get("category"),
                "side": fill.get("side"),
                "model_source": fill.get("model_source"),
                "q_effective": q_effective,
                "resolved_payout": payout,
                "resolved": payout is not None,
                "resolved_pnl_cents": resolved_pnl_cents,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    report = {
        "schema_version": f"{SCHEMA_VERSION}.resolved_audit_report",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_count": len(materialized_fills),
        "resolved_fill_count": len(pnl_values),
        "unresolved_fill_count": len(materialized_fills) - len(pnl_values),
        "resolved_pnl_cents": round(sum(pnl_values), 6) if pnl_values else None,
        "mean_resolved_pnl_cents": _mean(pnl_values),
        "by_category": _bucket_summary(audits, "category", "resolved_pnl_cents"),
        "by_model_source": _bucket_summary(audits, "model_source", "resolved_pnl_cents"),
        "audits": audits,
    }
    return report


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
    "build_markout_report",
    "build_resolved_audit_report",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
