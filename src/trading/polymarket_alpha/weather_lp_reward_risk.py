from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_risk.v1"
HORIZONS_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}
HORIZON_TOLERANCE_SECONDS = {"5m": 90, "15m": 180, "1h": 600}


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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _markout_value(row: Dict[str, Any]) -> Optional[float]:
    return _safe_float(row.get("markout_from_quote_price", row.get("price_markout_from_entry")))


def _markout_row_for_horizon(quote: Dict[str, Any], rows: List[Dict[str, Any]], horizon: str) -> Dict[str, Any]:
    quote_id = str(quote.get("quote_id") or "")
    start = _parse_utc(quote.get("entry_time") or quote.get("quote_start_time"))
    entry_midpoint = _safe_float(quote.get("entry_midpoint") or quote.get("midpoint"))
    entry_quote_price = _safe_float(quote.get("quote_price"))
    base = {
        "schema_version": f"{SCHEMA_VERSION}.markout_row",
        "quote_id": quote_id,
        "horizon": horizon,
        "quote_entry_time": _iso(start) if start else None,
        "target_time": None,
        "matched_update_time": None,
        "actual_elapsed_seconds": None,
        "horizon_match_status": "missing_quote_entry_time" if start is None else "missing_update_for_horizon",
        "entry_midpoint": entry_midpoint,
        "matched_midpoint": None,
        "entry_quote_price": entry_quote_price,
        "current_best_bid": None,
        "current_best_ask": None,
        "markout_cents": None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    if start is None:
        return base
    if horizon == "current":
        selected = _latest(rows)
        when = _parse_utc(selected.get("update_time") or selected.get("generated_at")) if selected else None
        base["target_time"] = None
        base["matched_update_time"] = _iso(when) if when else None
        base["actual_elapsed_seconds"] = round((when - start).total_seconds(), 3) if when else None
        base["horizon_match_status"] = "current_latest" if selected else "missing_update_for_horizon"
    else:
        horizon_seconds = HORIZONS_SECONDS[horizon]
        tolerance = HORIZON_TOLERANCE_SECONDS[horizon]
        target = start + timedelta(seconds=horizon_seconds)
        base["target_time"] = _iso(target)
        candidates: List[tuple[float, Dict[str, Any], datetime]] = []
        for row in rows:
            when = _parse_utc(row.get("update_time") or row.get("generated_at"))
            if when is None:
                continue
            lag = abs((when - target).total_seconds())
            if lag <= tolerance:
                candidates.append((lag, row, when))
        if candidates:
            lag, selected, when = sorted(candidates, key=lambda item: item[0])[0]
            base["matched_update_time"] = _iso(when)
            base["actual_elapsed_seconds"] = round((when - start).total_seconds(), 3)
            base["horizon_match_status"] = "within_tolerance"
        else:
            selected = {}
            when = None
    if not selected:
        return base
    matched_mid = _safe_float(selected.get("current_midpoint"))
    base["matched_midpoint"] = matched_mid
    base["current_best_bid"] = selected.get("current_best_bid")
    base["current_best_ask"] = selected.get("current_best_ask")
    base["markout_cents"] = _markout_value(selected)
    return base


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


def _percentile(values: Iterable[float], pct: float) -> Optional[float]:
    materialized = sorted(float(value) for value in values)
    if not materialized:
        return None
    index = min(len(materialized) - 1, max(0, int(round((len(materialized) - 1) * pct))))
    return round(materialized[index], 8)


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
    quote_summaries: List[Dict[str, Any]] = []
    markout_rows: List[Dict[str, Any]] = []
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
        horizon_rows = [_markout_row_for_horizon(quote, history, horizon) for horizon in ("5m", "15m", "1h", "current")]
        markout_rows.extend(horizon_rows)
        markout_by_horizon = {row["horizon"]: row.get("markout_cents") for row in horizon_rows}
        markout_loss = max(0.0, -float(current_markout or 0.0))
        quote_price = _safe_float(quote.get("quote_price"))
        quote_size = _safe_float(quote.get("quote_size") or quote.get("size"))
        capital_at_risk = round(float(quote_price or 0.0) * float(quote_size or 0.0) * 100.0, 8) if quote_price is not None and quote_size is not None else None
        break_even_share = 0.0 if markout_loss <= 0 else None
        summary = {
            "schema_version": f"{SCHEMA_VERSION}.quote_summary",
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
            "reward_allocation_if_available": quote.get("reward_allocation"),
            "total_market_q_score_if_available": quote.get("total_market_q_score"),
            "estimated_reward_cents_proxy": None,
            "estimated_reward_cents_proxy_gap_reason": "missing_reward_allocation_or_total_market_q_score",
            "reward_cents_if_share_0_1pct": None,
            "reward_cents_if_share_0_5pct": None,
            "reward_cents_if_share_1pct": None,
            "reward_cents_if_share_2pct": None,
            "break_even_reward_share": break_even_share,
            "competitor_total_q_required_to_break_even": None,
            "markout_loss_to_cover_cents": round(markout_loss, 8),
            "capital_at_risk": capital_at_risk,
            "markout_5m": markout_by_horizon.get("5m"),
            "markout_15m": markout_by_horizon.get("15m"),
            "markout_1h": markout_by_horizon.get("1h"),
            "current_markout": current_markout,
            "adverse_selection_count": adverse_count,
            "reward_points_per_cent_markout": reward_per_cent,
            "reward_points_to_markout_ratio": reward_per_cent,
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
        quote_summaries.append(summary)
    current_markouts = [row.get("current_markout") for row in quote_summaries if row.get("current_markout") is not None]
    negative_risk = sum(abs(float(value)) for value in current_markouts if float(value) < 0)
    reward_points_total = round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in quote_summaries), 8)
    reward_to_risk_proxy = round(reward_points_total / negative_risk, 8) if negative_risk else None
    valid_counts = Counter(row.get("horizon") for row in markout_rows if row.get("markout_cents") is not None and row.get("horizon_match_status") in {"within_tolerance", "current_latest"})
    missing_counts = Counter(str(row.get("horizon_match_status") or "unknown") for row in markout_rows if row.get("markout_cents") is None)
    break_even_values = [float(row.get("break_even_reward_share")) for row in quote_summaries if row.get("break_even_reward_share") is not None]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "paper_quote_count": len(quote_rows),
        "active_quote_count": len(quote_summaries),
        "update_count": len(update_rows),
        "quote_update_count": len(update_rows),
        "cumulative_reward_points_proxy": reward_points_total,
        "exact_reward_conversion_available_count": 0,
        "estimated_reward_cents_proxy_count": 0,
        "estimated_reward_cents_proxy": None,
        "estimated_reward_cents_proxy_gap_reason": "missing_reward_allocation_or_total_market_q_score",
        "break_even_share_median": _percentile(break_even_values, 0.5),
        "break_even_share_p90": _percentile(break_even_values, 0.9),
        "markets_where_0_5pct_share_covers_markout": None,
        "markets_where_1pct_share_covers_markout": None,
        "scenario_reward_0_5pct_share": None,
        "scenario_reward_1pct_share": None,
        "valid_markout_count_by_horizon": [
            {"horizon": horizon, "count": int(valid_counts.get(horizon, 0))}
            for horizon in ("5m", "15m", "1h", "current")
        ],
        "missing_horizon_reason_counts": [
            {"reason": key, "count": value}
            for key, value in sorted(missing_counts.items())
        ],
        "mean_5m_markout": _mean(row.get("markout_cents") for row in markout_rows if row.get("horizon") == "5m" and row.get("horizon_match_status") == "within_tolerance"),
        "mean_15m_markout": _mean(row.get("markout_cents") for row in markout_rows if row.get("horizon") == "15m" and row.get("horizon_match_status") == "within_tolerance"),
        "mean_1h_markout": _mean(row.get("markout_cents") for row in markout_rows if row.get("horizon") == "1h" and row.get("horizon_match_status") == "within_tolerance"),
        "mean_current_markout": _mean(row.get("markout_cents") for row in markout_rows if row.get("horizon") == "current" and row.get("horizon_match_status") == "current_latest"),
        "reward_to_risk_proxy": reward_to_risk_proxy,
        "price_markout_eating_reward_proxy": bool(reward_to_risk_proxy is not None and reward_to_risk_proxy < 1.0),
        "by_city": _group_rows(quote_summaries, "city"),
        "by_strategy_variant": _group_rows(quote_summaries, "strategy_variant"),
        "by_minute_of_hour": _group_by_quote_minute(quote_summaries, quote_rows),
        "by_bucket_type": _group_rows(quote_summaries, "bucket_type"),
        "by_basket_cost_bucket": _group_rows(quote_summaries, "basket_cost_bucket"),
        "rows": markout_rows,
        "quote_summaries": quote_summaries,
    }
    summary["city_basket_attribution"] = build_city_basket_attribution(summary, city_regimes=city_regimes)
    return summary


def _group_by_quote_minute(rows: List[Dict[str, Any]], quotes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    minute_by_quote = {str(row.get("quote_id") or ""): row.get("minute_of_hour") for row in quotes}
    augmented = [{**row, "minute_of_hour": minute_by_quote.get(str(row.get("quote_id") or ""))} for row in rows]
    return _group_rows(augmented, "minute_of_hour")


def build_city_basket_attribution(report: Dict[str, Any], *, city_regimes: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    rows = [row for row in report.get("quote_summaries") or report.get("rows") or [] if isinstance(row, dict)]
    regime_by_city = {str(row.get("city") or "").lower(): row for row in city_regimes if isinstance(row, dict)}
    by_city: List[Dict[str, Any]] = []
    for row in report.get("by_city") or []:
        city = str(row.get("city") or "")
        regime = regime_by_city.get(city.lower()) or {}
        by_city.append(
            {
                **row,
                "update_count": row.get("quote_count"),
                "valid_markout_count": row.get("quote_count") if row.get("mean_current_markout") is not None else 0,
                "city_regime": regime.get("volatility_bucket") or "unknown",
                "stable_volatile_classification": regime.get("volatility_bucket") or "unknown",
                "confidence": _city_confidence(int(row.get("quote_count") or 0)),
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


def _city_confidence(update_count: int) -> str:
    if update_count < 20:
        return "insufficient_sample"
    if update_count < 50:
        return "directional_hint"
    return "enough_for_filtering"


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
