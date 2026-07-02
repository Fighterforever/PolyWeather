from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_maker_quote_sweep.v1"


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


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 8) if values else None


def _mode_quote(mode: str, bid: float, ask: float) -> tuple[Optional[float], str]:
    spread = ask - bid
    if spread <= 0:
        return None, "invalid_spread"
    mid = (bid + ask) / 2.0
    tick = 0.01
    if mode == "passive_mid_plus_0_5c":
        price = mid - 0.005
        side = "maker_bid"
    elif mode == "passive_mid_plus_1c":
        price = mid - 0.01
        side = "maker_bid"
    elif mode == "passive_mid_plus_2c":
        price = mid - 0.02
        side = "maker_bid"
    elif mode == "join_best_bid":
        price = bid
        side = "maker_bid"
    elif mode == "improve_best_bid_by_1_tick":
        price = bid + tick
        side = "maker_bid"
    elif mode == "inside_spread_25pct":
        price = bid + spread * 0.25
        side = "maker_bid"
    elif mode == "inside_spread_50pct":
        price = bid + spread * 0.50
        side = "maker_bid"
    elif mode == "improve_best_ask_by_1_tick":
        price = ask - tick
        side = "maker_ask"
    else:
        return None, "unknown_mode"
    if side == "maker_bid" and not (bid <= price < ask):
        return None, "quote_not_inside_or_at_touch"
    if side == "maker_ask" and not (bid < price <= ask):
        return None, "quote_not_inside_or_at_touch"
    return round(max(0.001, min(0.999, price)), 8), side


def _rows_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict) and row.get("token_id"):
            grouped[str(row["token_id"])].append(row)
    for members in grouped.values():
        members.sort(key=lambda row: str(row.get("recorded_at") or row.get("timestamp") or ""))
    return grouped


def _first_touch(
    *,
    snapshots: List[Dict[str, Any]],
    entry_time: Optional[datetime],
    quote_price: float,
    quote_side: str,
) -> tuple[Optional[Dict[str, Any]], str]:
    for snapshot in snapshots:
        ts = _parse_utc(snapshot.get("recorded_at") or snapshot.get("timestamp"))
        if entry_time is not None and ts is not None and ts < entry_time:
            continue
        bid = _safe_float(snapshot.get("best_bid"))
        ask = _safe_float(snapshot.get("best_ask"))
        if quote_side == "maker_bid":
            if ask is not None and ask <= quote_price:
                return snapshot, "would_trade_through"
            if bid is not None and bid <= quote_price:
                return snapshot, "would_touch"
        else:
            if bid is not None and bid >= quote_price:
                return snapshot, "would_trade_through"
            if ask is not None and ask >= quote_price:
                return snapshot, "would_touch"
    return None, "not_touched"


def build_maker_quote_aggressiveness_sweep(
    *,
    maker_quotes: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    modes: Optional[List[str]] = None,
    estimated_rebate_cents: float = 0.0,
) -> Dict[str, Any]:
    quote_rows = [row for row in maker_quotes if isinstance(row, dict)]
    mode_list = modes or [
        "passive_mid_plus_0_5c",
        "passive_mid_plus_1c",
        "passive_mid_plus_2c",
        "join_best_bid",
        "improve_best_bid_by_1_tick",
        "improve_best_ask_by_1_tick",
        "inside_spread_25pct",
        "inside_spread_50pct",
    ]
    snapshots_by_token = _rows_by_token(orderbook_snapshots)
    rows: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()

    for quote in quote_rows:
        bid = _safe_float(quote.get("current_best_bid") or quote.get("best_bid"))
        ask = _safe_float(quote.get("current_best_ask") or quote.get("best_ask"))
        if bid is None or ask is None:
            spread = _safe_float(quote.get("spread"))
            q = _safe_float(quote.get("q_effective") or quote.get("quote_price"))
            if q is not None and spread is not None:
                bid = max(0.0, q - spread / 2.0)
                ask = min(1.0, q + spread / 2.0)
        token_id = str(quote.get("token_id") or "")
        entry_dt = _parse_utc(quote.get("entry_time") or quote.get("recorded_at") or quote.get("timestamp"))
        if bid is None or ask is None or not token_id:
            blockers["missing_quote_context"] += len(mode_list)
            continue
        snapshots = snapshots_by_token.get(token_id) or []
        for mode in mode_list:
            quote_price, quote_side = _mode_quote(mode, float(bid), float(ask))
            if quote_price is None:
                blockers[f"{mode}:{quote_side}"] += 1
                continue
            touch, touch_reason = _first_touch(snapshots=snapshots, entry_time=entry_dt, quote_price=quote_price, quote_side=quote_side)
            filled = touch is not None
            exit_bid = _safe_float(touch.get("best_bid")) if touch else None
            exit_ask = _safe_float(touch.get("best_ask")) if touch else None
            if filled and quote_side == "maker_bid" and exit_bid is not None:
                markout = round((exit_bid - quote_price) * 100.0, 6)
            elif filled and quote_side == "maker_ask" and exit_ask is not None:
                markout = round((quote_price - exit_ask) * 100.0, 6)
            else:
                markout = None
            rows.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.row",
                    "quote_id": quote.get("quote_id") or quote.get("candidate_id"),
                    "market_slug": quote.get("market_slug"),
                    "token_id": token_id,
                    "mode": mode,
                    "quote_side": quote_side,
                    "quote_price": quote_price,
                    "entry_time": quote.get("entry_time"),
                    "would_touch": touch_reason == "would_touch",
                    "would_trade_through": touch_reason == "would_trade_through",
                    "inferred_fill": filled,
                    "inferred_fill_confidence": 0.6 if touch_reason == "would_touch" else 0.85 if touch_reason == "would_trade_through" else 0.0,
                    "time_to_touch_seconds": _time_delta_seconds(entry_dt, _parse_utc(touch.get("recorded_at") or touch.get("timestamp")) if touch else None),
                    "touch_snapshot_time": touch.get("recorded_at") if touch else None,
                    "markout_after_touch": markout,
                    "adverse_selection_markout": markout if markout is not None and markout < 0 else None,
                    "estimated_rebate_cents": float(estimated_rebate_cents),
                    "pnl_without_rebate": markout,
                    "pnl_with_rebate": round(markout + float(estimated_rebate_cents), 6) if markout is not None else None,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )

    by_mode: List[Dict[str, Any]] = []
    for mode in mode_list:
        members = [row for row in rows if row.get("mode") == mode]
        fills = [row for row in members if row.get("inferred_fill")]
        markouts = [float(row["markout_after_touch"]) for row in fills if row.get("markout_after_touch") is not None]
        by_mode.append(
            {
                "mode": mode,
                "sweep_quote_count": len(members),
                "inferred_fill_count": len(fills),
                "mean_markout_cents": _mean(markouts),
                "adverse_selection_count": len([row for row in fills if _safe_float(row.get("markout_after_touch")) is not None and float(row["markout_after_touch"]) < 0]),
            }
        )
    fill_count = len([row for row in rows if row.get("inferred_fill")])
    filled_markouts = [float(row["markout_after_touch"]) for row in rows if row.get("inferred_fill") and row.get("markout_after_touch") is not None]
    best_mode = max(by_mode, key=lambda row: (row.get("mean_markout_cents") is not None, row.get("mean_markout_cents") or -999, row.get("inferred_fill_count") or 0), default=None)
    if fill_count == 0:
        recommendation = "maker_no_fill_even_aggressive"
    elif filled_markouts and _mean(filled_markouts) is not None and (_mean(filled_markouts) or 0.0) < 0:
        recommendation = "maker_adverse_selection"
    elif best_mode and (best_mode.get("mean_markout_cents") or 0.0) > 0:
        recommendation = "promising_shadow_mode"
    else:
        recommendation = "reject_maker_for_now"
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "original_quote_count": len(quote_rows),
        "sweep_quote_count": len(rows),
        "inferred_fill_count": fill_count,
        "inferred_fill_count_by_mode": by_mode,
        "mean_markout_by_mode": by_mode,
        "adverse_selection_by_mode": [{"mode": row["mode"], "adverse_selection_count": row["adverse_selection_count"]} for row in by_mode],
        "best_mode": best_mode.get("mode") if best_mode else None,
        "mode_recommendation": recommendation,
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(blockers.items())],
        "rows": rows,
    }


def _time_delta_seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 6)


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
    "build_maker_quote_aggressiveness_sweep",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
