from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_touch_forward_validation.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _mean(values: Iterable[Any]) -> Optional[float]:
    parsed = [float(value) for value in (_safe_float(value) for value in values) if value is not None]
    return round(sum(parsed) / len(parsed), 8) if parsed else None


def _horizon_key(row: Dict[str, Any]) -> str:
    value = row.get("horizon_seconds")
    if value is None:
        value = row.get("horizon")
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return str(row.get("horizon_label") or "missing")
    return {
        300: "5m",
        900: "15m",
        3600: "1h",
        21600: "6h",
        86400: "24h",
    }.get(seconds, "current" if seconds == 0 else f"{seconds}s")


def _mean_by_horizon(rows: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        value = _safe_float(row.get("markout_cents"))
        if value is not None:
            grouped[_horizon_key(row)].append(value)
    return {key: _mean(values) for key, values in sorted(grouped.items())}


def _count_by_key(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get(key) or "missing")] += 1
    return dict(sorted(counts.items()))


def _bucket_rows(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        value = _safe_float(row.get("markout_cents"))
        if value is not None:
            grouped[str(row.get(key) or "missing")].append(value)
    return [
        {"bucket": bucket, "available_markout_count": len(values), "mean_markout_cents": _mean(values)}
        for bucket, values in sorted(grouped.items())
    ]


def _threshold_distance_bucket(fill: Dict[str, Any]) -> str:
    threshold = _safe_float(fill.get("threshold"))
    max_high = _safe_float(fill.get("max_high_since_start"))
    if threshold is None or max_high is None:
        return "missing"
    distance = threshold - max_high
    if distance <= 0:
        return "already_touched_or_at_barrier"
    if distance <= threshold * 0.05:
        return "within_5pct"
    if distance <= threshold * 0.15:
        return "within_15pct"
    return "far_gt_15pct"


def _spread_bucket(fill: Dict[str, Any]) -> str:
    spread = _safe_float(fill.get("spread"))
    if spread is None:
        return "missing"
    if spread <= 0.03:
        return "spread_le_3c"
    if spread <= 0.07:
        return "spread_3c_to_7c"
    return "spread_gt_7c"


def _formal_fill_summary(fills: List[Dict[str, Any]], markouts: List[Dict[str, Any]]) -> Dict[str, Any]:
    available = [row for row in markouts if row.get("markout_cents") is not None]
    by_horizon = _mean_by_horizon(available)
    threshold_groups: Dict[str, List[float]] = defaultdict(list)
    spread_groups: Dict[str, List[float]] = defaultdict(list)
    fill_by_id = {str(row.get("fill_id") or row.get("token_id")): row for row in fills}
    for row in available:
        fill = fill_by_id.get(str(row.get("fill_id") or row.get("token_id"))) or {}
        threshold_groups[_threshold_distance_bucket(fill)].append(float(row["markout_cents"]))
        spread_groups[_spread_bucket(fill)].append(float(row["markout_cents"]))
    return {
        "fill_count": len(fills),
        "mean_EV_safe": _mean(fill.get("EV_safe") for fill in fills),
        "available_markout_count": len(available),
        "mean_5m_markout": by_horizon.get("5m"),
        "mean_15m_markout": by_horizon.get("15m"),
        "mean_1h_markout": by_horizon.get("1h"),
        "mean_6h_markout": by_horizon.get("6h"),
        "mean_24h_markout": by_horizon.get("24h"),
        "by_horizon": by_horizon,
        "by_asset": _bucket_rows(available, "asset"),
        "by_threshold_distance": [
            {"bucket": bucket, "available_markout_count": len(values), "mean_markout_cents": _mean(values)}
            for bucket, values in sorted(threshold_groups.items())
        ],
        "by_spread": [
            {"bucket": bucket, "available_markout_count": len(values), "mean_markout_cents": _mean(values)}
            for bucket, values in sorted(spread_groups.items())
        ],
    }


def _horizon_markout_validity(markouts: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    by_horizon: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in markouts:
        horizon = _horizon_key(row)
        status = str(row.get("horizon_match_status") or row.get("missing_snapshot_reason") or "missing")
        by_horizon[horizon][status] += 1
    return {horizon: dict(sorted(counts.items())) for horizon, counts in sorted(by_horizon.items())}


def _surface_support_count(fills: List[Dict[str, Any]], surface_report: Optional[Dict[str, Any]]) -> int:
    if not isinstance(surface_report, dict):
        return 0
    supported = {
        str(row.get("market_slug"))
        for row in (surface_report.get("rows") or []) + (surface_report.get("top_relative_value_rows") or [])
        if isinstance(row, dict) and row.get("surface_supports_model_direction") is True
    }
    return sum(1 for fill in fills if str(fill.get("market_slug")) in supported)


def _near_miss_summary(watches: List[Dict[str, Any]], markouts: List[Dict[str, Any]]) -> Dict[str, Any]:
    available = [row for row in markouts if row.get("markout_cents") is not None]
    by_horizon = _mean_by_horizon(available)
    return {
        "watch_count": len(watches),
        "mean_EV_safe": _mean(row.get("EV_safe") for row in watches),
        "available_markout_count": len(available),
        "mean_5m_markout": by_horizon.get("5m"),
        "mean_15m_markout": by_horizon.get("15m"),
        "mean_1h_markout": by_horizon.get("1h"),
        "mean_6h_markout": by_horizon.get("6h"),
        "mean_24h_markout": by_horizon.get("24h"),
        "by_horizon": by_horizon,
        "by_asset": _bucket_rows(available, "asset"),
    }


def _invalidated_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = defaultdict(int)
    invalidated_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("invalidated") or row.get("invalidation_reason") or row.get("action") not in {None, "keep"}:
            invalidated_count += 1
            counts[str(row.get("invalidation_reason") or row.get("reason") or row.get("action") or "unknown")] += 1
    return {
        "invalidated_count": invalidated_count,
        "reason_counts": [{"reason": reason, "count": count} for reason, count in sorted(counts.items())],
    }


def build_crypto_touch_forward_validation_report(
    *,
    formal_fills: Iterable[Dict[str, Any]],
    formal_markouts: Iterable[Dict[str, Any]],
    near_miss_watch: Iterable[Dict[str, Any]],
    near_miss_markouts: Iterable[Dict[str, Any]],
    invalidated_old_fills: Iterable[Dict[str, Any]] = (),
    followup_coverage_report: Optional[Dict[str, Any]] = None,
    surface_report: Optional[Dict[str, Any]] = None,
    sensitivity_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fills = [row for row in formal_fills if isinstance(row, dict)]
    formal_markout_rows = [row for row in formal_markouts if isinstance(row, dict)]
    watch_rows = [row for row in near_miss_watch if isinstance(row, dict)]
    near_markout_rows = [row for row in near_miss_markouts if isinstance(row, dict)]
    formal = _formal_fill_summary(fills, formal_markout_rows)
    near = _near_miss_summary(watch_rows, near_markout_rows)
    horizon_validity = _horizon_markout_validity(formal_markout_rows)
    non_current_markouts = [
        row for row in formal_markout_rows
        if row.get("markout_cents") is not None and _horizon_key(row) != "current"
    ]
    formal_non_current_negative = bool(
        non_current_markouts
        and _mean(row.get("markout_cents") for row in non_current_markouts) is not None
        and (_mean(row.get("markout_cents") for row in non_current_markouts) or 0.0) < 0
    )
    surface_support_count = _surface_support_count(fills, surface_report)
    sensitivity_fragile_count = int((sensitivity_report or {}).get("sensitivity_fragile_count") or 0)
    near_short_negative = any(
        value is not None and value < 0
        for value in (near.get("mean_5m_markout"), near.get("mean_15m_markout"))
    )
    formal_values = [
        value
        for value in (formal.get("mean_5m_markout"), formal.get("mean_15m_markout"), formal.get("mean_1h_markout"), formal.get("mean_6h_markout"))
        if value is not None
    ]
    formal_negative = bool(formal_values and _mean(formal_values) is not None and _mean(formal_values) < 0)
    formal_1h_6h_positive = (
        formal.get("mean_1h_markout") is not None
        and formal.get("mean_6h_markout") is not None
        and formal["mean_1h_markout"] > 0
        and formal["mean_6h_markout"] > 0
    )
    if formal["fill_count"] > 0 and not non_current_markouts:
        verdict = "crypto_touch_waiting_for_valid_horizon_markout"
    elif formal_non_current_negative and surface_support_count < formal["fill_count"]:
        verdict = "crypto_touch_model_overoptimistic_reduce_priority"
    elif formal_values and _mean(formal_values) is not None and (_mean(formal_values) or 0.0) > 0 and surface_support_count > 0:
        verdict = "continue_crypto_touch_sampling"
    elif formal["fill_count"] < 20:
        verdict = "continue_crypto_touch_sampling_insufficient_forward_fills"
    elif formal_negative:
        verdict = "crypto_touch_forward_markout_negative_reduce_priority"
    elif formal_1h_6h_positive:
        verdict = "continue_crypto_touch_sampling"
    else:
        verdict = "continue_crypto_touch_sampling_waiting_for_markout"
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "formal_fills": formal,
        "formal_horizon_coverage": (followup_coverage_report or {}).get("coverage_by_horizon") if isinstance(followup_coverage_report, dict) else {},
        "horizon_markout_validity": horizon_validity,
        "surface_support_count": surface_support_count,
        "sensitivity_fragile_count": sensitivity_fragile_count,
        "near_miss_watch": near,
        "invalidated_old_fills": _invalidated_summary(invalidated_old_fills),
        "verdict": {
            "status": verdict,
            "continue_crypto_touch_sampling": bool(verdict in {"continue_crypto_touch_sampling", "continue_crypto_touch_sampling_insufficient_forward_fills"}),
            "keep_min_edge_threshold": True,
            "do_not_lower_threshold": bool(near_short_negative),
            "reduce_priority_if_negative_markout": bool(verdict in {"crypto_touch_model_overoptimistic_reduce_priority", "crypto_touch_forward_markout_negative_reduce_priority"}),
            "paper_only_review_required": True,
        },
    }


__all__ = [
    "SCHEMA_VERSION",
    "build_crypto_touch_forward_validation_report",
    "write_json",
    "write_jsonl",
]
