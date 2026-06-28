from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_trade_tape_backfill import (
    locked_signal_rows_from_report,
    locked_side_token_id,
    token_id_by_outcome,
    write_json,
    write_jsonl,
)
from src.weather.weather_sources import parse_utc


SCHEMA_VERSION = "polyweather_observation_lock_trade_replay.v1"
WINDOWS: Tuple[Tuple[str, int, int], ...] = (
    ("0-1m", 0, 1),
    ("1-3m", 1, 3),
    ("3-5m", 3, 5),
    ("5-15m", 5, 15),
    ("15-30m", 15, 30),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _closed_by_slug(closed_markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for row in closed_markets:
        if not isinstance(row, dict):
            continue
        slug = _text(row.get("market_slug") or row.get("slug"))
        if slug and slug not in selected:
            selected[slug] = row
    return selected


def _trade_index(trades: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in trades:
        if not isinstance(row, dict):
            continue
        slug = _text(row.get("market_slug"))
        token = _text(row.get("token_id") or row.get("asset"))
        if not slug or not token:
            continue
        grouped[(slug, token)].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: parse_utc(row.get("timestamp")) or datetime.max.replace(tzinfo=timezone.utc))
    return grouped


def _locked_side_payout(signal: Dict[str, Any], closed_record: Optional[Dict[str, Any]]) -> Optional[float]:
    direct = _safe_float(signal.get("payout"))
    if direct is not None:
        return direct
    if not closed_record:
        return None
    yes = _safe_float(closed_record.get("settled_yes_payout"))
    if yes is None:
        probabilities = closed_record.get("settled_probability_by_outcome")
        if isinstance(probabilities, dict):
            yes = _safe_float(probabilities.get("Yes") if "Yes" in probabilities else probabilities.get("yes"))
    if yes is None:
        return None
    return yes if _text(signal.get("locked_side")).upper() == "YES" else 1.0 - yes


def _time_to_close_bucket(value: Any) -> str:
    minutes = _safe_float(value)
    if minutes is None:
        return "unknown"
    if minutes < 30:
        return "lt_30m"
    if minutes < 120:
        return "30m_to_2h"
    return "gt_2h"


def _count_by(rows: Iterable[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    counts = Counter(_text(row.get(field)) or "unknown" for row in rows)
    return [{field: key, "count": count} for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]


def _proxy_summary(rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_text(row.get(field)) or "unknown"].append(row)
    output: List[Dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        candidates = [row for row in group if row.get("approximate_hit_price") is not None]
        resolved = [row for row in candidates if row.get("trade_replay_pnl_cents") is not None]
        pnl = sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved)
        output.append(
            {
                field: key,
                "row_count": len(group),
                "trade_proxy_candidate_count": len(candidates),
                "trade_proxy_positive_count": len(
                    [row for row in resolved if float(row.get("trade_replay_pnl_cents") or 0.0) > 0.0]
                ),
                "trade_proxy_pnl_cents": round(pnl, 6) if resolved else None,
            }
        )
    return output


def build_observation_lock_trade_replay_report(
    *,
    tradability_report: Dict[str, Any],
    trade_tape_rows: Iterable[Dict[str, Any]],
    closed_markets: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    signals = locked_signal_rows_from_report(tradability_report)
    closed_by_slug = _closed_by_slug(closed_markets)
    trades_by_market_token = _trade_index(trade_tape_rows)
    rows: List[Dict[str, Any]] = []
    signal_has_any_trade_0_5m: set[int] = set()
    signal_has_any_trade: set[int] = set()
    signal_covered: set[int] = set()
    for signal_index, signal in enumerate(signals):
        slug = _text(signal.get("market_slug"))
        record = closed_by_slug.get(slug)
        locked_side = _text(signal.get("locked_side")).upper()
        target_token = locked_side_token_id(signal, record)
        replay_dt = parse_utc(signal.get("replay_time"))
        payout = _locked_side_payout(signal, record)
        max_entry = _safe_float(signal.get("approximate_price"))
        candidate_trades = trades_by_market_token.get((slug, target_token or ""), [])
        if candidate_trades:
            signal_covered.add(signal_index)
        for window, start_min, end_min in WINDOWS:
            reason = "ok"
            matching: List[Dict[str, Any]] = []
            if not target_token:
                reason = "missing_locked_side_token"
            elif replay_dt is None:
                reason = "missing_replay_time"
            elif not candidate_trades:
                reason = "missing_same_token_trade"
            else:
                start = replay_dt + timedelta(minutes=start_min)
                end = replay_dt + timedelta(minutes=end_min)
                for trade in candidate_trades:
                    trade_dt = parse_utc(trade.get("timestamp"))
                    if trade_dt is None:
                        continue
                    if start <= trade_dt <= end:
                        matching.append(trade)
                if not matching:
                    reason = "no_trade_in_window"
            prices = [_safe_float(row.get("price")) for row in matching]
            prices = [price for price in prices if price is not None]
            at_or_better: List[Dict[str, Any]] = []
            if max_entry is not None:
                at_or_better = [
                    trade for trade in matching if _safe_float(trade.get("price")) is not None and float(trade["price"]) <= max_entry
                ]
            if matching:
                signal_has_any_trade.add(signal_index)
                if end_min <= 5:
                    signal_has_any_trade_0_5m.add(signal_index)
            if matching and max_entry is None:
                reason = "missing_max_entry_price"
            if matching and max_entry is not None and not at_or_better:
                reason = "trade_above_max_entry_price"
            hit_trade = at_or_better[0] if at_or_better else None
            hit_price = _safe_float((hit_trade or {}).get("price"))
            pnl = round((float(payout) - float(hit_price)) * 100.0, 6) if payout is not None and hit_price is not None else None
            rows.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.row",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                    "market_slug": slug or None,
                    "token_id": target_token,
                    "source_signal_token_id": signal.get("token_id"),
                    "station_code": signal.get("station_code"),
                    "target_date": signal.get("target_date"),
                    "bucket_type": signal.get("bucket_type"),
                    "threshold": signal.get("threshold"),
                    "locked_side": locked_side or None,
                    "lock_state": signal.get("lock_state"),
                    "replay_time": signal.get("replay_time"),
                    "window": window,
                    "trade_count": len(matching),
                    "first_trade_at": matching[0].get("timestamp") if matching else None,
                    "best_trade_price_for_locked_side": min(prices) if prices else None,
                    "worst_trade_price_for_locked_side": max(prices) if prices else None,
                    "size_at_or_better": round(
                        sum(float(_safe_float(trade.get("size")) or 0.0) for trade in at_or_better),
                        6,
                    ),
                    "max_entry_price_for_locked_side": max_entry,
                    "approximate_hit_price": hit_price,
                    "payout": payout,
                    "trade_replay_pnl_cents": pnl,
                    "can_count_as_real_pnl": False,
                    "can_count_as_trade_proxy": hit_price is not None,
                    "reason": reason,
                    "time_to_close_bucket": signal.get("time_to_close_bucket") or _time_to_close_bucket(signal.get("time_to_close")),
                    "trade_direction_confidence": (hit_trade or {}).get("direction_confidence"),
                    "trade_source_trade_id": (hit_trade or {}).get("source_trade_id"),
                }
            )
    candidates = [row for row in rows if row.get("approximate_hit_price") is not None]
    resolved = [row for row in candidates if row.get("trade_replay_pnl_cents") is not None]
    total_pnl = sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved)
    summary = {
        "schema_version": f"{SCHEMA_VERSION}.summary",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "locked_signal_count": len(signals),
        "trade_tape_covered_count": len(signal_covered),
        "any_trade_after_signal_count": len(signal_has_any_trade),
        "same_token_trade_0_5m_count": len(signal_has_any_trade_0_5m),
        "trade_proxy_candidate_count": len(candidates),
        "trade_proxy_positive_count": len(
            [row for row in resolved if float(row.get("trade_replay_pnl_cents") or 0.0) > 0.0]
        ),
        "trade_proxy_pnl_cents": round(total_pnl, 6) if resolved else None,
        "missing_trade_count": len([row for row in rows if row.get("trade_count") == 0]),
        "by_station": _proxy_summary(rows, "station_code"),
        "by_bucket_type": _proxy_summary(rows, "bucket_type"),
        "by_window": _proxy_summary(rows, "window"),
        "by_time_to_close": _proxy_summary(rows, "time_to_close_bucket"),
        "reason_counts": _count_by(rows, "reason"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": summary,
        "rows": rows,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "SCHEMA_VERSION",
    "WINDOWS",
    "build_observation_lock_trade_replay_report",
    "load_json",
    "write_json",
    "write_jsonl",
]
