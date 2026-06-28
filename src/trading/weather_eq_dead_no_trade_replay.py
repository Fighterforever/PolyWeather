from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional


WINDOW_ORDER = {"0-1m": 0, "1-3m": 1, "3-5m": 2, "5-15m": 3, "15-30m": 4}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_price_band(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "price_unknown"
    if price <= 0.80:
        return "price_le_0_80"
    if price <= 0.95:
        return "price_0_80_to_0_95"
    return "price_gt_0_95"


def _group(rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_text(row.get(field)) or "unknown"].append(row)
    output = []
    for key, group in sorted(grouped.items()):
        resolved = [row for row in group if row.get("trade_replay_pnl_cents") is not None]
        output.append(
            {
                field: key,
                "trade_proxy_candidate_count": len(group),
                "trade_proxy_pnl_cents": round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved), 6) if resolved else None,
            }
        )
    return output


def _pnl_sum(rows: Iterable[Dict[str, Any]]) -> Optional[float]:
    resolved = [row for row in rows if row.get("trade_replay_pnl_cents") is not None]
    if not resolved:
        return None
    return round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved), 6)


def _signal_key(row: Dict[str, Any]) -> tuple[Any, Any, Any]:
    return (row.get("market_slug"), row.get("token_id"), row.get("replay_time"))


def _dedupe_first_window(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    first_by_signal: Dict[tuple[Any, Any, Any], Dict[str, Any]] = {}
    for row in rows:
        key = _signal_key(row)
        current = first_by_signal.get(key)
        if current is None or WINDOW_ORDER.get(_text(row.get("window")), 999) < WINDOW_ORDER.get(_text(current.get("window")), 999):
            first_by_signal[key] = row
    return list(first_by_signal.values())


def _is_high_direction(row: Dict[str, Any]) -> bool:
    return _text(row.get("trade_direction_confidence")).lower() in {"reported_by_polymarket_data_api", "high"}


def _first_trade_delay_seconds(row: Dict[str, Any]) -> Optional[float]:
    replay_time = _parse_utc(row.get("replay_time"))
    first_trade = _parse_utc(row.get("first_trade_at"))
    if replay_time is None or first_trade is None:
        return None
    return round((first_trade - replay_time).total_seconds(), 6)


def _numeric_summary(values: Iterable[Any]) -> Dict[str, Optional[float]]:
    nums = [_safe_float(value) for value in values]
    nums = [float(value) for value in nums if value is not None]
    if not nums:
        return {"sum": None, "median": None, "min": None, "max": None}
    return {
        "sum": round(sum(nums), 6),
        "median": round(float(median(nums)), 6),
        "min": round(min(nums), 6),
        "max": round(max(nums), 6),
    }


def _conservative_proxy_rows(rows: Iterable[Dict[str, Any]], *, default_cost: float = 0.005) -> List[Dict[str, Any]]:
    eligible: List[Dict[str, Any]] = []
    for row in rows:
        price = _safe_float(row.get("approximate_hit_price"))
        payout = _safe_float(row.get("payout"))
        if row.get("bucket_type") != "eq":
            continue
        if row.get("lock_state") != "eq_yes_dead_no_locked" or row.get("locked_side") != "NO":
            continue
        if price is None or payout is None:
            continue
        if not _text(row.get("token_id")) or not _text(row.get("trade_source_trade_id")):
            continue
        if not _is_high_direction(row):
            continue
        edge_after_cost = float(payout) - float(price) - float(default_cost)
        if price >= 0.98 and edge_after_cost <= 0:
            continue
        eligible.append(
            {
                **row,
                "can_count_as_real_pnl": False,
                "can_count_as_trade_proxy": True,
                "conservative_proxy": True,
                "edge_after_cost": round(edge_after_cost, 6),
            }
        )
    return _dedupe_first_window(eligible)


def build_eq_dead_no_proxy_robustness_report(
    *,
    observation_lock_trade_rows: Iterable[Dict[str, Any]],
    default_cost: float = 0.005,
) -> Dict[str, Any]:
    base = build_eq_dead_no_trade_replay_report(observation_lock_trade_rows=observation_lock_trade_rows)
    report_rows = [row for row in base.get("rows") or [] if isinstance(row, dict)]
    raw_candidates = [row for row in report_rows if row.get("approximate_hit_price") is not None]
    deduped = _dedupe_first_window(raw_candidates)
    high_confidence = [row for row in raw_candidates if _is_high_direction(row)]
    null_direction = [
        row
        for row in raw_candidates
        if not _text(row.get("trade_direction_confidence"))
        or _text(row.get("trade_direction_confidence")).lower() in {"unknown", "null", "none"}
    ]
    unknown_excluded = [row for row in raw_candidates if not _is_high_direction(row)]

    deduped_sorted = sorted(
        [row for row in deduped if row.get("trade_replay_pnl_cents") is not None],
        key=lambda row: float(row.get("trade_replay_pnl_cents") or 0.0),
        reverse=True,
    )
    deduped_total = _pnl_sum(deduped) or 0.0
    top_1 = float(deduped_sorted[0].get("trade_replay_pnl_cents") or 0.0) if deduped_sorted else 0.0
    top_2 = sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in deduped_sorted[:2])
    by_market: Dict[str, float] = defaultdict(float)
    for row in deduped_sorted:
        by_market[_text(row.get("market_slug")) or "unknown"] += float(row.get("trade_replay_pnl_cents") or 0.0)
    top_market, top_market_pnl = max(by_market.items(), key=lambda item: item[1], default=("unknown", 0.0))

    delays = [_first_trade_delay_seconds(row) for row in deduped]
    delay_summary = _numeric_summary(delay for delay in delays if delay is not None)
    conservative = _conservative_proxy_rows(raw_candidates, default_cost=default_cost)
    conservative_sorted = sorted(
        [row for row in conservative if row.get("trade_replay_pnl_cents") is not None],
        key=lambda row: float(row.get("trade_replay_pnl_cents") or 0.0),
        reverse=True,
    )
    conservative_pnl = _pnl_sum(conservative)
    conservative_top_1 = float(conservative_sorted[0].get("trade_replay_pnl_cents") or 0.0) if conservative_sorted else 0.0
    conservative_without_top_1 = (
        round(float(conservative_pnl) - conservative_top_1, 6) if conservative_pnl is not None else None
    )

    raw_summary = {
        "raw_trade_proxy_candidate_count": len(raw_candidates),
        "raw_trade_proxy_pnl_cents": _pnl_sum(raw_candidates),
        "deduped_trade_proxy_candidate_count": len(deduped),
        "deduped_trade_proxy_pnl_cents": _pnl_sum(deduped),
    }
    direction_split = {
        "high_confidence_candidate_count": len(high_confidence),
        "high_confidence_pnl_cents": _pnl_sum(high_confidence),
        "null_direction_candidate_count": len(null_direction),
        "null_direction_pnl_cents": _pnl_sum(null_direction),
        "unknown_direction_excluded_pnl_cents": _pnl_sum(unknown_excluded),
        "high_confidence_proxy_pnl_cents": _pnl_sum(high_confidence),
    }
    outlier_analysis = {
        "top_1_contribution_cents": round(top_1, 6) if deduped_sorted else None,
        "top_1_contribution_pct": round((top_1 / deduped_total) * 100.0, 6) if deduped_total else None,
        "pnl_without_top_1": round(deduped_total - top_1, 6) if deduped_sorted else None,
        "pnl_without_top_2": round(deduped_total - top_2, 6) if deduped_sorted else None,
        "top_market_slug": top_market if by_market else None,
        "top_market_contribution_cents": round(top_market_pnl, 6) if by_market else None,
        "pnl_without_top_market": round(deduped_total - top_market_pnl, 6) if by_market else None,
        "unique_market_count": len({_text(row.get("market_slug")) for row in deduped if _text(row.get("market_slug"))}),
        "unique_station_count": len({_text(row.get("station_code")) for row in deduped if _text(row.get("station_code"))}),
        "unique_day_count": len({_text(row.get("target_date")) for row in deduped if _text(row.get("target_date"))}),
    }
    liquidity_realism = {
        "size_at_or_better_sum": _numeric_summary(row.get("size_at_or_better") for row in deduped)["sum"],
        "median_size_at_or_better": _numeric_summary(row.get("size_at_or_better") for row in deduped)["median"],
        "min_size_at_or_better": _numeric_summary(row.get("size_at_or_better") for row in deduped)["min"],
        "trade_count_by_signal": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "replay_time": row.get("replay_time"),
                "window": row.get("window"),
                "trade_count": int(row.get("trade_count") or 0),
            }
            for row in sorted(deduped, key=lambda item: (_text(item.get("market_slug")), _text(item.get("replay_time"))))
        ],
        "first_trade_delay_seconds": delay_summary,
    }
    strict_conservative_proxy = {
        "conservative_candidate_count": len(conservative),
        "conservative_proxy_pnl_cents": conservative_pnl,
        "conservative_proxy_pnl_without_top_1": conservative_without_top_1,
        "conservative_proxy_positive_after_outlier_removal": bool(
            conservative_without_top_1 is not None and conservative_without_top_1 > 0
        ),
        "can_count_as_real_pnl": False,
        "can_count_as_trade_proxy": True,
        "sample_conservative_rows": conservative_sorted[:10],
    }
    summary = {
        **raw_summary,
        **direction_split,
        **outlier_analysis,
        **strict_conservative_proxy,
        "proxy_robust_enough_for_forward_paper": bool(
            strict_conservative_proxy["conservative_proxy_positive_after_outlier_removal"]
            and (direction_split["high_confidence_pnl_cents"] or 0.0) > 0.0
        ),
    }
    return {
        "schema_version": "polyweather_eq_dead_no_proxy_robustness.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "raw_summary": raw_summary,
        "direction_confidence_split": direction_split,
        "outlier_analysis": outlier_analysis,
        "liquidity_realism": liquidity_realism,
        "strict_conservative_proxy": strict_conservative_proxy,
        "summary": summary,
    }


def build_eq_dead_no_trade_replay_report(*, observation_lock_trade_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [
        row
        for row in observation_lock_trade_rows
        if isinstance(row, dict)
        and row.get("bucket_type") == "eq"
        and row.get("lock_state") == "eq_yes_dead_no_locked"
        and row.get("locked_side") == "NO"
    ]
    candidates = [row for row in rows if row.get("approximate_hit_price") is not None]
    deduped = _dedupe_first_window(candidates)
    pnl_rows = [row for row in candidates if row.get("trade_replay_pnl_cents") is not None]
    deduped_pnl_rows = [row for row in deduped if row.get("trade_replay_pnl_cents") is not None]
    missing_direct_trade_count = len([row for row in rows if row.get("trade_count") == 0])
    report_rows = [
        {
            **row,
            "strategy_id": "eq_dead_no_lock",
            "entry_price_band": _entry_price_band(row.get("approximate_hit_price")),
            "counts_for_live_gate": False,
            "live_order_path": False,
            "can_count_as_real_pnl": False,
            "can_count_as_trade_proxy": bool(row.get("approximate_hit_price") is not None),
        }
        for row in rows
    ]
    top = sorted(
        [row for row in report_rows if row.get("approximate_hit_price") is not None],
        key=lambda row: float(row.get("trade_replay_pnl_cents") or 0.0),
        reverse=True,
    )[:20]
    return {
        "schema_version": "polyweather_eq_dead_no_trade_replay.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": {
            "breached_eq_signal_count": len(
                {
                    (row.get("market_slug"), row.get("token_id"), row.get("replay_time"))
                    for row in rows
                }
            ),
            "no_trade_count": missing_direct_trade_count,
            "trade_proxy_candidate_count": len(candidates),
            "trade_proxy_pnl_cents": round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in pnl_rows), 6) if pnl_rows else None,
            "deduped_trade_proxy_candidate_count": len(deduped),
            "deduped_trade_proxy_pnl_cents": round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in deduped_pnl_rows), 6) if deduped_pnl_rows else None,
            "by_station": _group(deduped, "station_code"),
            "by_time_to_close": _group(deduped, "time_to_close_bucket"),
            "by_entry_price_band": _group(
                [{**row, "entry_price_band": _entry_price_band(row.get("approximate_hit_price"))} for row in deduped],
                "entry_price_band",
            ),
            "top_samples": top,
            "missing_direct_trade_count": missing_direct_trade_count,
        },
        "rows": report_rows,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows = []
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
