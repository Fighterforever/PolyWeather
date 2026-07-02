from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple


MODEL_COVERAGE_SCHEMA_VERSION = "polyweather_weather_model_coverage.v1"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(row: Dict[str, Any], fields: Iterable[str]) -> Optional[float]:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    return None


def _order_book_depth(row: Dict[str, Any], field: str) -> Optional[float]:
    value = _safe_float(row.get(field))
    if value is not None:
        return value
    order_book = row.get("order_book") if isinstance(row.get("order_book"), dict) else {}
    return _safe_float(order_book.get(field))


def _price(row: Dict[str, Any]) -> Optional[float]:
    return _first_float(
        row,
        (
            "price",
            "ask",
            "best_ask",
            "market_probability",
            "yes_price",
            "no_price",
        ),
    )


def _spread(row: Dict[str, Any]) -> Optional[float]:
    return _first_float(row, ("spread", "entry_spread", "yes_spread", "no_spread"))


def _liquidity(row: Dict[str, Any]) -> Optional[float]:
    return _first_float(
        row,
        (
            "execution_liquidity",
            "book_liquidity",
            "liquidity",
            "liquidityNum",
            "liquidityClob",
            "volume",
        ),
    )


def _market_family(row: Dict[str, Any]) -> str:
    return str(row.get("market_family") or "unknown").strip().lower() or "unknown"


def _model_status(row: Dict[str, Any]) -> str:
    return str(row.get("model_join_status") or "unknown").strip().lower() or "unknown"


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
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


def _horizon_days(row: Dict[str, Any], generated_at_dt: datetime) -> Optional[float]:
    end_dt = _parse_utc_datetime(row.get("end_date"))
    if end_dt is None:
        return None
    return round((end_dt - generated_at_dt).total_seconds() / 86400.0, 6)


def _is_tradable_surface(
    row: Dict[str, Any],
    *,
    min_price: float,
    max_price: float,
    max_spread: float,
    min_liquidity: float,
    min_bid_depth_usdc_3c: float,
    min_ask_depth_usdc_3c: float,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if row.get("active") is False:
        reasons.append("market_inactive")
    if row.get("closed") is True:
        reasons.append("market_closed")
    if row.get("tradable") is False:
        reasons.append("row_not_tradable")
    if row.get("accepting_orders") is False:
        reasons.append("not_accepting_orders")

    price = _price(row)
    spread = _spread(row)
    liquidity = _liquidity(row)
    bid_depth = _order_book_depth(row, "bid_depth_usdc_3c")
    ask_depth = _order_book_depth(row, "ask_depth_usdc_3c")

    if price is None:
        reasons.append("missing_price")
    elif price < float(min_price):
        reasons.append("price_below_min")
    elif price > float(max_price):
        reasons.append("price_above_max")

    if spread is None:
        reasons.append("missing_spread")
    elif spread > float(max_spread):
        reasons.append("spread_above_max")

    if liquidity is None:
        reasons.append("missing_liquidity")
    elif liquidity < float(min_liquidity):
        reasons.append("liquidity_below_min")

    if min_bid_depth_usdc_3c > 0:
        if bid_depth is None:
            reasons.append("missing_bid_depth")
        elif bid_depth < float(min_bid_depth_usdc_3c):
            reasons.append("bid_depth_below_min")
    if min_ask_depth_usdc_3c > 0:
        if ask_depth is None:
            reasons.append("missing_ask_depth")
        elif ask_depth < float(min_ask_depth_usdc_3c):
            reasons.append("ask_depth_below_min")

    return not reasons, reasons


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _median(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(float(median(materialized)), 6)


def _sample(row: Dict[str, Any], blockers: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "market_family": _market_family(row),
        "model_join_status": _model_status(row),
        "event_title": row.get("event_title"),
        "question": row.get("question"),
        "market_id": row.get("market_id"),
        "market_slug": row.get("market_slug") or row.get("slug"),
        "token_id": row.get("token_id"),
        "side": row.get("side"),
        "outcome": row.get("outcome"),
        "price": _price(row),
        "spread": _spread(row),
        "liquidity": _liquidity(row),
        "bid_depth_usdc_3c": _order_book_depth(row, "bid_depth_usdc_3c"),
        "ask_depth_usdc_3c": _order_book_depth(row, "ask_depth_usdc_3c"),
        "end_date": row.get("end_date"),
        "surface_blockers": blockers or [],
    }


def _recommended_model_action(market_family: str) -> str:
    if market_family == "rain":
        return "build_precipitation_probability_model"
    if market_family == "snow":
        return "build_snowfall_probability_model"
    if market_family == "hurricane":
        return "build_hurricane_resolution_model_or_keep_no_trade"
    if market_family == "air_quality":
        return "build_air_quality_probability_model"
    if market_family == "climate":
        return "exclude_from_short_horizon_weather_bot"
    if market_family == "weather_other":
        return "inspect_market_text_then_route_or_exclude"
    return "inspect_before_model_build"


def build_weather_model_coverage_report(
    payload: Dict[str, Any],
    *,
    min_price: float = 0.03,
    max_price: float = 0.85,
    max_spread: float = 0.02,
    min_liquidity: float = 10.0,
    min_bid_depth_usdc_3c: float = 10.0,
    min_ask_depth_usdc_3c: float = 10.0,
    max_model_build_horizon_days: float = 14.0,
    max_samples_per_family: int = 5,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Quantify non-temperature weather markets that are blocked only by model coverage.

    This report is diagnostic-only. It does not make unsupported markets tradable; it
    ranks where a new model family would have enough market surface to justify work.
    """

    generated = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    generated_at_dt = _parse_utc_datetime(generated) or datetime.now(timezone.utc).replace(microsecond=0)
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)] if isinstance(payload, dict) else []
    groups: Dict[str, Dict[str, Any]] = {}
    overall_surface_ready = 0
    overall_model_gap_surface_ready = 0
    overall_short_horizon_model_gap_surface_ready = 0
    overall_long_horizon_model_gap_surface_ready = 0
    status_counts: Dict[str, int] = {}
    family_counts: Dict[str, int] = {}

    for row in rows:
        family = _market_family(row)
        status = _model_status(row)
        family_counts[family] = family_counts.get(family, 0) + 1
        status_counts[status] = status_counts.get(status, 0) + 1
        surface_ready, surface_blockers = _is_tradable_surface(
            row,
            min_price=min_price,
            max_price=max_price,
            max_spread=max_spread,
            min_liquidity=min_liquidity,
            min_bid_depth_usdc_3c=min_bid_depth_usdc_3c,
            min_ask_depth_usdc_3c=min_ask_depth_usdc_3c,
        )
        if surface_ready:
            overall_surface_ready += 1

        group = groups.setdefault(
            family,
            {
                "market_family": family,
                "row_count": 0,
                "joined_count": 0,
                "unsupported_count": 0,
                "missing_model_count": 0,
                "other_model_gap_count": 0,
                "surface_ready_count": 0,
                "model_gap_surface_ready_count": 0,
                "short_horizon_model_gap_surface_ready_count": 0,
                "long_horizon_model_gap_surface_ready_count": 0,
                "liquidity_values": [],
                "spread_values": [],
                "short_horizon_liquidity_values": [],
                "short_horizon_spread_values": [],
                "long_horizon_liquidity_values": [],
                "long_horizon_spread_values": [],
                "samples": [],
                "short_horizon_samples": [],
                "long_horizon_samples": [],
                "blocked_surface_reason_counts": {},
            },
        )
        group["row_count"] += 1
        if status == "joined":
            group["joined_count"] += 1
        elif status == "unsupported_market_type":
            group["unsupported_count"] += 1
        elif status == "missing_scan_model_row":
            group["missing_model_count"] += 1
        elif status != "unknown":
            group["other_model_gap_count"] += 1

        if surface_ready:
            group["surface_ready_count"] += 1
        else:
            for reason in surface_blockers:
                counts = group["blocked_surface_reason_counts"]
                counts[reason] = counts.get(reason, 0) + 1

        model_gap = status != "joined"
        if model_gap and surface_ready:
            group["model_gap_surface_ready_count"] += 1
            overall_model_gap_surface_ready += 1
            liquidity = _liquidity(row)
            spread = _spread(row)
            horizon = _horizon_days(row, generated_at_dt)
            short_horizon = horizon is not None and 0 <= horizon <= float(max_model_build_horizon_days)
            if liquidity is not None:
                group["liquidity_values"].append(float(liquidity))
            if spread is not None:
                group["spread_values"].append(float(spread))
            if len(group["samples"]) < max(0, int(max_samples_per_family)):
                group["samples"].append(_sample(row))
            if short_horizon:
                group["short_horizon_model_gap_surface_ready_count"] += 1
                overall_short_horizon_model_gap_surface_ready += 1
                if liquidity is not None:
                    group["short_horizon_liquidity_values"].append(float(liquidity))
                if spread is not None:
                    group["short_horizon_spread_values"].append(float(spread))
                if len(group["short_horizon_samples"]) < max(0, int(max_samples_per_family)):
                    sample = _sample(row)
                    sample["horizon_days"] = horizon
                    group["short_horizon_samples"].append(sample)
            else:
                group["long_horizon_model_gap_surface_ready_count"] += 1
                overall_long_horizon_model_gap_surface_ready += 1
                if liquidity is not None:
                    group["long_horizon_liquidity_values"].append(float(liquidity))
                if spread is not None:
                    group["long_horizon_spread_values"].append(float(spread))
                if len(group["long_horizon_samples"]) < max(0, int(max_samples_per_family)):
                    sample = _sample(row)
                    sample["horizon_days"] = horizon
                    group["long_horizon_samples"].append(sample)

    family_rows: List[Dict[str, Any]] = []
    model_build_queue: List[Dict[str, Any]] = []
    long_horizon_watch_queue: List[Dict[str, Any]] = []
    for family, group in groups.items():
        row_count = int(group["row_count"])
        gap_count = int(group["model_gap_surface_ready_count"])
        short_gap_count = int(group["short_horizon_model_gap_surface_ready_count"])
        long_gap_count = int(group["long_horizon_model_gap_surface_ready_count"])
        family_row = {
            "market_family": family,
            "row_count": row_count,
            "joined_count": int(group["joined_count"]),
            "unsupported_count": int(group["unsupported_count"]),
            "missing_model_count": int(group["missing_model_count"]),
            "other_model_gap_count": int(group["other_model_gap_count"]),
            "surface_ready_count": int(group["surface_ready_count"]),
            "model_gap_surface_ready_count": gap_count,
            "short_horizon_model_gap_surface_ready_count": short_gap_count,
            "long_horizon_model_gap_surface_ready_count": long_gap_count,
            "model_gap_surface_ready_rate": round(gap_count / row_count, 6) if row_count else None,
            "mean_model_gap_liquidity": _mean(group["liquidity_values"]),
            "median_model_gap_spread": _median(group["spread_values"]),
            "mean_short_horizon_model_gap_liquidity": _mean(group["short_horizon_liquidity_values"]),
            "median_short_horizon_model_gap_spread": _median(group["short_horizon_spread_values"]),
            "mean_long_horizon_model_gap_liquidity": _mean(group["long_horizon_liquidity_values"]),
            "median_long_horizon_model_gap_spread": _median(group["long_horizon_spread_values"]),
            "blocked_surface_reason_counts": [
                {"reason": reason, "count": count}
                for reason, count in sorted(
                    group["blocked_surface_reason_counts"].items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ],
            "samples": group["samples"],
            "short_horizon_samples": group["short_horizon_samples"],
            "long_horizon_samples": group["long_horizon_samples"],
        }
        family_rows.append(family_row)
        if family != "temperature" and short_gap_count > 0:
            model_build_queue.append(
                {
                    "market_family": family,
                    "model_gap_surface_ready_count": short_gap_count,
                    "row_count": row_count,
                    "mean_model_gap_liquidity": family_row["mean_short_horizon_model_gap_liquidity"],
                    "median_model_gap_spread": family_row["median_short_horizon_model_gap_spread"],
                    "max_model_build_horizon_days": float(max_model_build_horizon_days),
                    "recommended_action": _recommended_model_action(family),
                    "samples": group["short_horizon_samples"][:3],
                }
            )
        if family != "temperature" and long_gap_count > 0:
            long_horizon_watch_queue.append(
                {
                    "market_family": family,
                    "long_horizon_model_gap_surface_ready_count": long_gap_count,
                    "row_count": row_count,
                    "mean_model_gap_liquidity": family_row["mean_long_horizon_model_gap_liquidity"],
                    "median_model_gap_spread": family_row["median_long_horizon_model_gap_spread"],
                    "max_model_build_horizon_days": float(max_model_build_horizon_days),
                    "recommended_action": "watch_long_horizon_or_route_to_separate_research",
                    "samples": group["long_horizon_samples"][:3],
                }
            )

    family_rows.sort(
        key=lambda row: (
            -int(row.get("model_gap_surface_ready_count") or 0),
            -int(row.get("row_count") or 0),
            str(row.get("market_family") or ""),
        )
    )
    model_build_queue.sort(
        key=lambda row: (
            -int(row.get("model_gap_surface_ready_count") or 0),
            -(float(row.get("mean_model_gap_liquidity") or 0.0)),
            str(row.get("market_family") or ""),
        )
    )
    long_horizon_watch_queue.sort(
        key=lambda row: (
            -int(row.get("long_horizon_model_gap_surface_ready_count") or 0),
            -(float(row.get("mean_model_gap_liquidity") or 0.0)),
            str(row.get("market_family") or ""),
        )
    )

    if model_build_queue:
        hard_conclusion = "non_temperature_model_gap_detected"
    elif long_horizon_watch_queue:
        hard_conclusion = "non_temperature_model_gap_long_horizon_only"
    elif any(family != "temperature" for family in family_counts):
        hard_conclusion = "non_temperature_surface_not_tradeable_yet"
    elif family_counts.get("temperature", 0) > 0:
        hard_conclusion = "coverage_currently_temperature_only"
    else:
        hard_conclusion = "no_weather_rows_seen"

    return {
        "schema_version": MODEL_COVERAGE_SCHEMA_VERSION,
        "generated_at": generated,
        "source_snapshot_id": payload.get("snapshot_id") if isinstance(payload, dict) else None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "thresholds": {
            "min_price": float(min_price),
            "max_price": float(max_price),
            "max_spread": float(max_spread),
            "min_liquidity": float(min_liquidity),
            "min_bid_depth_usdc_3c": float(min_bid_depth_usdc_3c),
            "min_ask_depth_usdc_3c": float(min_ask_depth_usdc_3c),
            "max_model_build_horizon_days": float(max_model_build_horizon_days),
        },
        "total_rows": len(rows),
        "surface_ready_count": overall_surface_ready,
        "model_gap_surface_ready_count": overall_model_gap_surface_ready,
        "short_horizon_model_gap_surface_ready_count": overall_short_horizon_model_gap_surface_ready,
        "long_horizon_model_gap_surface_ready_count": overall_long_horizon_model_gap_surface_ready,
        "family_counts": [
            {"market_family": family, "count": count}
            for family, count in sorted(family_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "model_join_status_counts": [
            {"model_join_status": status, "count": count}
            for status, count in sorted(status_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "family_coverage": family_rows,
        "model_build_queue": model_build_queue,
        "model_build_queue_count": len(model_build_queue),
        "long_horizon_watch_queue": long_horizon_watch_queue,
        "long_horizon_watch_queue_count": len(long_horizon_watch_queue),
        "hard_conclusion": hard_conclusion,
    }
