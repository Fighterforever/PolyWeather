from __future__ import annotations

import json
import math
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_probability_edge_journal.v1"
CRYPTO_TOUCH_REQUIRED_FIELDS = (
    "semantics_type",
    "probability_semantics",
    "market_creation_time",
    "high_since_start_verified",
    "barrier_already_touched",
    "max_high_since_start",
    "p_yes_touch",
    "p_no_touch",
    "p_trade_lcb",
)


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


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_id(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


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


def _active_market_by_token(active_markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for market in active_markets:
        if not isinstance(market, dict):
            continue
        for token_id in market.get("token_ids") or []:
            mapping[str(token_id)] = market
    return mapping


def _book_for_candidate(candidate: Dict[str, Any], active_by_token: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    embedded = candidate.get("orderbook_snapshot")
    if isinstance(embedded, dict) and embedded:
        return embedded, "candidate_embedded_orderbook"
    token_id = str(candidate.get("token_id") or "")
    market = active_by_token.get(token_id) or {}
    books = market.get("orderbooks") if isinstance(market.get("orderbooks"), dict) else {}
    book = books.get(token_id) if isinstance(books.get(token_id), dict) else {}
    if book:
        return book, "active_market_orderbook"
    return {
        "best_bid": candidate.get("best_bid") if candidate.get("side") == "YES" else None,
        "best_ask": candidate.get("best_ask") or candidate.get("q_effective"),
        "spread": candidate.get("spread"),
        "ask_depth_usdc_3c": candidate.get("depth"),
        "bid_ladder": [],
        "ask_ladder": [],
    }, "candidate_quote_fallback"


def build_orderbook_snapshot(
    candidate: Dict[str, Any],
    *,
    active_by_token: Optional[Dict[str, Dict[str, Any]]] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    active_by_token = active_by_token or {}
    recorded_at = recorded_at or candidate.get("generated_at") or _iso_now()
    book, source = _book_for_candidate(candidate, active_by_token)
    token_id = str(candidate.get("token_id") or "")
    snapshot = {
        "schema_version": f"{SCHEMA_VERSION}.orderbook_snapshot",
        "orderbook_snapshot_id": _stable_id(
            {
                "market_slug": candidate.get("market_slug"),
                "token_id": token_id,
                "recorded_at": recorded_at,
                "best_bid": book.get("best_bid"),
                "best_ask": book.get("best_ask"),
            }
        ),
        "market_slug": candidate.get("market_slug"),
        "token_id": token_id,
        "best_bid": _safe_float(book.get("best_bid")),
        "best_ask": _safe_float(book.get("best_ask")),
        "bid_ladder": book.get("bid_ladder") if isinstance(book.get("bid_ladder"), list) else [],
        "ask_ladder": book.get("ask_ladder") if isinstance(book.get("ask_ladder"), list) else [],
        "spread": _safe_float(book.get("spread")),
        "depth": _safe_float(book.get("ask_depth_usdc_3c") or book.get("depth")),
        "recorded_at": recorded_at,
        "timestamp": recorded_at,
        "source": source,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    return snapshot


def build_probability_edge_fills_with_snapshots(
    *,
    candidates: Iterable[Dict[str, Any]],
    active_markets: Iterable[Dict[str, Any]] = (),
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    active_by_token = _active_market_by_token(active_markets)
    fills: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        rejection_reason = _crypto_touch_fill_rejection_reason(candidate)
        if rejection_reason:
            rejected.append(
                {
                    "market_slug": candidate.get("market_slug"),
                    "token_id": candidate.get("token_id"),
                    "reason": rejection_reason,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
            continue
        snapshot = build_orderbook_snapshot(candidate, active_by_token=active_by_token, recorded_at=recorded_at)
        snapshots.append(snapshot)
        fill = dict(candidate)
        fill["schema_version"] = f"{SCHEMA_VERSION}.fill"
        fill["timestamp"] = fill.get("timestamp") or fill.get("generated_at") or snapshot.get("recorded_at")
        fill["generated_at"] = fill.get("generated_at") or snapshot.get("recorded_at")
        fill["orderbook_snapshot_id"] = snapshot.get("orderbook_snapshot_id")
        fill["paper_only"] = True
        fill["counts_for_live_gate"] = False
        fill["live_order_path"] = False
        fill.pop("orderbook_snapshot", None)
        fills.append(fill)
    return {
        "fills": fills,
        "orderbook_snapshots": snapshots,
        "orderbook_snapshot_id_null_count": len([row for row in fills if not row.get("orderbook_snapshot_id")]),
        "rejected_fill_count": len(rejected),
        "rejected_fills": rejected,
    }


def _crypto_touch_fill_rejection_reason(candidate: Dict[str, Any]) -> Optional[str]:
    is_crypto_touch = (
        str(candidate.get("model_source") or "") == "crypto_lognormal_threshold_model"
        or str(candidate.get("probability_semantics") or "") == "touch_barrier"
        or str(candidate.get("semantics_type") or "") == "touch_barrier"
    )
    if not is_crypto_touch:
        return None
    missing = [field for field in CRYPTO_TOUCH_REQUIRED_FIELDS if candidate.get(field) is None]
    if missing:
        return f"missing_crypto_touch_fields:{','.join(missing)}"
    if str(candidate.get("semantics_type")) != "touch_barrier" or str(candidate.get("probability_semantics")) != "touch_barrier":
        return "crypto_touch_semantics_mismatch"
    if not bool(candidate.get("high_since_start_verified")):
        return "start_time_unverified_or_high_unverified"
    if bool(candidate.get("barrier_already_touched")):
        return "barrier_already_touched"
    return None


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
    orderbook_snapshots: Iterable[Dict[str, Any]] = (),
    horizons: Tuple[int, ...] = (60, 300, 900, 3600),
) -> Dict[str, Any]:
    materialized_fills = [row for row in fills if isinstance(row, dict)]
    histories = _by_token(price_rows)
    snapshot_histories = _by_token(orderbook_snapshots)
    markouts: List[Dict[str, Any]] = []
    missing_snapshot_count = 0
    for fill in materialized_fills:
        token_id = str(fill.get("token_id") or "")
        entry_time = _parse_utc(fill.get("timestamp") or fill.get("generated_at"))
        q_effective = _safe_float(fill.get("q_effective"))
        if not token_id or entry_time is None or q_effective is None:
            missing_snapshot_count += 1
            continue
        token_history = histories.get(token_id) or []
        token_snapshots = snapshot_histories.get(token_id) or []
        for horizon in tuple(horizons) + (0,):
            future = _first_after(token_snapshots, entry_time, horizon) if token_snapshots else None
            future_source = "orderbook_snapshot"
            if future is None:
                future = _first_after(token_history, entry_time, horizon)
                future_source = "price_row"
            if future is None:
                missing_snapshot_count += 1
                continue
            exit_price = _safe_float(future.get("best_bid"))
            if exit_price is None:
                exit_price = _safe_float(future.get("price_mid") or future.get("price"))
            if exit_price is None:
                missing_snapshot_count += 1
                continue
            markout_cents = round((exit_price - q_effective) * 100.0, 6)
            markouts.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.markout",
                    "market_slug": fill.get("market_slug"),
                    "token_id": token_id,
                    "category": fill.get("category"),
                    "side": fill.get("side"),
                    "asset": fill.get("asset"),
                    "semantics_type": fill.get("semantics_type"),
                    "model_source": fill.get("model_source"),
                    "entry_time": fill.get("timestamp") or fill.get("generated_at"),
                    "future_time": future.get("timestamp") or future.get("recorded_at"),
                    "horizon_seconds": horizon,
                    "horizon_label": "current" if horizon == 0 else f"{horizon}s",
                    "q_effective": q_effective,
                    "future_side_price": round(exit_price, 8),
                    "future_source": future_source,
                    "markout": round(exit_price - q_effective, 8),
                    "markout_cents": markout_cents,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    one_hour = [row for row in markouts if int(row.get("horizon_seconds") or 0) == 3600]
    all_values = [_safe_float(row.get("markout_cents")) for row in markouts]
    all_values = [value for value in all_values if value is not None]
    one_hour_values = [_safe_float(row.get("markout_cents")) for row in one_hour]
    one_hour_values = [value for value in one_hour_values if value is not None]
    report = {
        "schema_version": f"{SCHEMA_VERSION}.markout_report",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_count": len(materialized_fills),
        "markout_count": len(markouts),
        "available_markout_count": len(markouts),
        "missing_snapshot_count": missing_snapshot_count,
        "mean_markout": _mean([value / 100.0 for value in all_values]),
        "mean_markout_cents": _mean(all_values),
        "mean_markout_1h": _mean([value / 100.0 for value in one_hour_values]),
        "mean_markout_1h_cents": _mean(one_hour_values),
        "markout_status": (
            "ready_waiting_for_probability_edge_fills"
            if not materialized_fills
            else "no_forward_markout_yet"
            if not markouts
            else "forward_markout_positive"
            if (_mean(one_hour_values) or _mean(all_values) or 0.0) > 0
            else "forward_markout_nonpositive"
        ),
        "by_horizon": _bucket_summary(markouts, "horizon_label", "markout_cents"),
        "by_asset": _bucket_summary(markouts, "asset", "markout_cents"),
        "by_semantics_type": _bucket_summary(markouts, "semantics_type", "markout_cents"),
        "by_side": _bucket_summary(markouts, "side", "markout_cents"),
        "by_category": _bucket_summary(markouts, "category", "markout_cents"),
        "by_model_source": _bucket_summary(markouts, "model_source", "markout_cents"),
        "markouts": markouts,
    }
    if not materialized_fills:
        report["markout_status"] = "ready_waiting_for_valid_crypto_fills"
        report["legacy_markout_status"] = "ready_waiting_for_probability_edge_fills"
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
    "build_orderbook_snapshot",
    "build_markout_report",
    "build_probability_edge_fills_with_snapshots",
    "build_resolved_audit_report",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
