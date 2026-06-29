from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_risk.v1"
HORIZONS_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _quote_updates_by_quote(updates: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in updates:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        if quote_id:
            buckets[quote_id].append(row)
    for quote_id, rows in buckets.items():
        rows.sort(key=lambda row: _parse_utc(row.get("update_time") or row.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return buckets


def _latest(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return rows[-1] if rows else {}


def _markout_for_horizon(quote: Dict[str, Any], rows: List[Dict[str, Any]], horizon_seconds: int) -> Optional[float]:
    start = _parse_utc(quote.get("quote_start_time"))
    if start is None:
        return None
    target = start + timedelta(seconds=horizon_seconds)
    eligible = [
        row
        for row in rows
        if (_parse_utc(row.get("update_time") or row.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= target
        and row.get("price_markout_from_entry") is not None
    ]
    if not eligible:
        return None
    return _safe_float(eligible[0].get("price_markout_from_entry"))


def _bucket_cost(value: Any) -> str:
    parsed = _safe_float(value)
    if parsed is None:
        return "missing"
    if parsed < 0.80:
        return "<0.80"
    if parsed < 0.90:
        return "0.80-0.90"
    if parsed < 0.95:
        return "0.90-0.95"
    if parsed < 0.98:
        return "0.95-0.98"
    return ">=0.98"


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    materialized = [float(value) for value in values if value is not None]
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 8)


def _group_rows(rows: Iterable[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(field) or "missing")].append(row)
    output: List[Dict[str, Any]] = []
    for key, bucket_rows in sorted(buckets.items()):
        output.append(
            {
                field: key,
                "quote_count": len(bucket_rows),
                "reward_points_proxy": round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in bucket_rows), 8),
                "mean_current_markout": _mean(row.get("current_markout") for row in bucket_rows),
                "adverse_selection_count": sum(int(row.get("adverse_selection_count") or 0) for row in bucket_rows),
            }
        )
    return output


def build_weather_lp_reward_risk_report(
    *,
    quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
    city_regimes: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    quote_rows = [row for row in quotes if isinstance(row, dict)]
    update_rows = [row for row in quote_updates if isinstance(row, dict)]
    updates_by_quote = _quote_updates_by_quote(update_rows)
    regime_by_city = {str(row.get("city") or "").lower(): row for row in city_regimes if isinstance(row, dict)}
    rows: List[Dict[str, Any]] = []
    for quote in quote_rows:
        quote_id = str(quote.get("quote_id") or "")
        history = updates_by_quote.get(quote_id, [])
        latest = _latest(history)
        start = _parse_utc(quote.get("quote_start_time"))
        latest_time = _parse_utc(latest.get("update_time") or latest.get("generated_at"))
        time_on_book = None
        if start is not None and latest_time is not None:
            time_on_book = max(0.0, (latest_time - start).total_seconds())
        current_markout = _safe_float(latest.get("price_markout_from_entry"))
        cumulative = _safe_float(latest.get("cumulative_reward_points_proxy"))
        adverse_count = sum(1 for row in history if row.get("adverse_selection_flag") or row.get("adverse_selection"))
        reward_per_cent = None
        if cumulative is not None and current_markout not in (None, 0):
            reward_per_cent = round(cumulative / abs(float(current_markout)), 8)
        row = {
            "schema_version": f"{SCHEMA_VERSION}.row",
            "quote_id": quote_id,
            "strategy_variant": quote.get("strategy_variant"),
            "city": quote.get("city"),
            "station_code": quote.get("station_code"),
            "bucket_type": quote.get("bucket_type"),
            "threshold": quote.get("threshold"),
            "entry_quote_price": quote.get("quote_price"),
            "latest_midpoint": latest.get("current_midpoint"),
            "latest_best_bid": latest.get("current_best_bid"),
            "latest_best_ask": latest.get("current_best_ask"),
            "time_on_book_seconds": time_on_book,
            "cumulative_reward_points_proxy": cumulative,
            "estimated_reward_cents_proxy": None,
            "estimated_reward_cents_proxy_gap_reason": "missing_reward_allocation_or_total_market_q_score",
            "markout_5m": _markout_for_horizon(quote, history, 300),
            "markout_15m": _markout_for_horizon(quote, history, 900),
            "markout_1h": _markout_for_horizon(quote, history, 3600),
            "current_markout": current_markout,
            "adverse_selection_count": adverse_count,
            "reward_points_per_cent_markout": reward_per_cent,
            "net_proxy_without_reward": current_markout,
            "net_proxy_with_reward_if_available": None,
            "risk_adjusted_reward_score": round(float(cumulative or 0.0) - max(0.0, -float(current_markout or 0.0)), 8),
            "basket_total_cost": quote.get("basket_cost"),
            "basket_cost_bucket": _bucket_cost(quote.get("basket_cost")),
            "city_regime": (regime_by_city.get(str(quote.get("city") or "").lower()) or {}).get("volatility_bucket"),
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        rows.append(row)
    current_markouts = [row.get("current_markout") for row in rows if row.get("current_markout") is not None]
    negative_risk = sum(abs(float(value)) for value in current_markouts if float(value) < 0)
    reward_points_total = round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in rows), 8)
    reward_to_risk_proxy = round(reward_points_total / negative_risk, 8) if negative_risk else None
    summary = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "paper_quote_count": len(quote_rows),
        "active_quote_count": len(rows),
        "update_count": len(update_rows),
        "quote_update_count": len(update_rows),
        "cumulative_reward_points_proxy": reward_points_total,
        "estimated_reward_cents_proxy": None,
        "estimated_reward_cents_proxy_gap_reason": "missing_reward_allocation_or_total_market_q_score",
        "mean_5m_markout": _mean(row.get("markout_5m") for row in rows),
        "mean_15m_markout": _mean(row.get("markout_15m") for row in rows),
        "mean_1h_markout": _mean(row.get("markout_1h") for row in rows),
        "mean_current_markout": _mean(row.get("current_markout") for row in rows),
        "reward_to_risk_proxy": reward_to_risk_proxy,
        "price_markout_eating_reward_proxy": bool(reward_to_risk_proxy is not None and reward_to_risk_proxy < 1.0),
        "by_city": _group_rows(rows, "city"),
        "by_strategy_variant": _group_rows(rows, "strategy_variant"),
        "by_minute_of_hour": _group_by_quote_minute(rows, quote_rows),
        "by_bucket_type": _group_rows(rows, "bucket_type"),
        "by_basket_cost_bucket": _group_rows(rows, "basket_cost_bucket"),
        "rows": rows,
    }
    summary["city_basket_attribution"] = build_city_basket_attribution(summary, city_regimes=city_regimes)
    return summary


def _group_by_quote_minute(rows: List[Dict[str, Any]], quotes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    minute_by_quote = {str(row.get("quote_id") or ""): row.get("minute_of_hour") for row in quotes}
    augmented = [{**row, "minute_of_hour": minute_by_quote.get(str(row.get("quote_id") or ""))} for row in rows]
    return _group_rows(augmented, "minute_of_hour")


def build_city_basket_attribution(report: Dict[str, Any], *, city_regimes: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    rows = [row for row in report.get("rows") or [] if isinstance(row, dict)]
    regime_by_city = {str(row.get("city") or "").lower(): row for row in city_regimes if isinstance(row, dict)}
    by_city: List[Dict[str, Any]] = []
    for row in report.get("by_city") or []:
        city = str(row.get("city") or "")
        regime = regime_by_city.get(city.lower()) or {}
        by_city.append(
            {
                **row,
                "city_regime": regime.get("volatility_bucket") or "unknown",
                "stable_volatile_classification": regime.get("volatility_bucket") or "unknown",
                "recommended_city_rules": _recommended_city_rule(row, regime),
            }
        )
    by_cost = report.get("by_basket_cost_bucket") or []
    return {
        "schema_version": f"{SCHEMA_VERSION}.city_basket_attribution",
        "by_city": by_city,
        "by_basket_cost_bucket": by_cost,
        "expensive_basket_rejection_count": len([row for row in rows if str(row.get("basket_cost_bucket")) == ">=0.98"]),
        "expensive_basket_markout_if_watch_only": _mean(row.get("current_markout") for row in rows if str(row.get("basket_cost_bucket")) in {"0.95-0.98", ">=0.98"}),
        "recommended_city_rules": [
            {"city": row.get("city"), "rule": row.get("recommended_city_rules")}
            for row in by_city
        ],
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _recommended_city_rule(row: Dict[str, Any], regime: Dict[str, Any]) -> str:
    markout = _safe_float(row.get("mean_current_markout"))
    bucket = str(regime.get("volatility_bucket") or "unknown")
    if markout is not None and markout < -1.0:
        return "tighten_or_pause_city_quotes"
    if bucket == "stable":
        return "prefer_narrow_low_cost_quotes"
    if bucket == "volatile":
        return "require_wider_reward_buffer"
    return "collect_more_city_specific_updates"


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
    "build_weather_lp_reward_risk_report",
    "build_city_basket_attribution",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
