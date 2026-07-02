from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_microstructure_experiment_analysis.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 8) if values else None


def _bucket_numeric(value: Any, cuts: List[float], labels: List[str]) -> str:
    parsed = _safe_float(value)
    if parsed is None:
        return "missing"
    for cut, label in zip(cuts, labels):
        if parsed < cut:
            return label
    return labels[-1] if labels else "value"


def _markout_summary(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        value = _safe_float(row.get("markout_cents"))
        if value is None:
            continue
        grouped[str(row.get(key) or "missing")].append(value)
    return [
        {
            "bucket": bucket,
            "sample_count": len(values),
            "mean_markout_cents": _mean(values),
        }
        for bucket, values in sorted(grouped.items())
    ]


def _join_context(
    *,
    markouts: Iterable[Dict[str, Any]],
    fills: Iterable[Dict[str, Any]],
    candidates: Iterable[Dict[str, Any]],
    watch_rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    fill_by_id = {str(row.get("fill_id") or row.get("candidate_id")): row for row in fills if isinstance(row, dict)}
    candidate_by_id = {str(row.get("candidate_id")): row for row in candidates if isinstance(row, dict) and row.get("candidate_id")}
    watch_by_id = {str(row.get("watch_id")): row for row in watch_rows if isinstance(row, dict) and row.get("watch_id")}
    joined: List[Dict[str, Any]] = []
    for markout in markouts:
        if not isinstance(markout, dict) or markout.get("markout_cents") is None:
            continue
        fill = fill_by_id.get(str(markout.get("fill_id") or markout.get("candidate_id"))) or {}
        candidate = candidate_by_id.get(str(markout.get("candidate_id"))) or {}
        watch = watch_by_id.get(str(fill.get("source_watch_id") or candidate.get("source_watch_id") or markout.get("watch_id"))) or {}
        q_effective = _safe_float(fill.get("q_effective") or candidate.get("q_effective") or markout.get("q_effective"))
        spread = _safe_float(fill.get("spread") or candidate.get("spread") or watch.get("spread"))
        imbalance = _safe_float(fill.get("imbalance") or candidate.get("imbalance") or watch.get("depth_imbalance"))
        row = {
            **markout,
            "policy_id": markout.get("policy_id") or fill.get("policy_id") or candidate.get("policy_id"),
            "category": markout.get("category") or fill.get("category") or candidate.get("category") or watch.get("category"),
            "spread": spread,
            "depth_imbalance": imbalance,
            "q_effective": q_effective,
            "time_to_close": watch.get("time_to_close"),
            "spread_bucket": _bucket_numeric(spread, [0.02, 0.04, 0.08, 0.15], ["spread_lt_2c", "spread_2_4c", "spread_4_8c", "spread_8_15c", "spread_ge_15c"]),
            "depth_imbalance_bucket": _bucket_numeric(imbalance, [-0.75, -0.4, 0.4, 0.75], ["imbalance_le_neg_75", "imbalance_neg_75_to_neg_40", "imbalance_mid", "imbalance_pos_40_to_75", "imbalance_ge_75"]),
            "price_bucket": _bucket_numeric(q_effective, [0.1, 0.3, 0.7, 0.9], ["price_lt_10c", "price_10_30c", "price_30_70c", "price_70_90c", "price_ge_90c"]),
        }
        row["time_to_close_bucket"] = _time_to_close_bucket(row.get("entry_time"), row.get("time_to_close"))
        joined.append(row)
    return joined


def _time_to_close_bucket(entry_time: Any, close_time: Any) -> str:
    from datetime import datetime, timezone

    def parse(value: Any) -> Optional[datetime]:
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

    entry = parse(entry_time)
    close = parse(close_time)
    if entry is None or close is None:
        return "missing"
    hours = (close - entry).total_seconds() / 3600.0
    if hours < 1:
        return "lt_1h"
    if hours < 24:
        return "1h_24h"
    if hours < 7 * 24:
        return "1d_7d"
    if hours < 30 * 24:
        return "7d_30d"
    return "ge_30d"


def build_microstructure_taker_failure_attribution(
    *,
    fills: Iterable[Dict[str, Any]],
    markouts: Iterable[Dict[str, Any]],
    candidates: Iterable[Dict[str, Any]] = (),
    watch_rows: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    fill_rows = [row for row in fills if isinstance(row, dict)]
    markout_rows = [row for row in markouts if isinstance(row, dict)]
    joined = _join_context(markouts=markout_rows, fills=fill_rows, candidates=candidates, watch_rows=watch_rows)
    available = [row for row in joined if row.get("markout_cents") is not None]
    horizons = _markout_summary(available, "horizon_seconds")
    horizon_means = {str(row["bucket"]): row.get("mean_markout_cents") for row in horizons}
    key_horizons = [horizon_means.get("300"), horizon_means.get("900"), horizon_means.get("3600")]
    all_key_negative = bool(key_horizons) and all(value is not None and value < 0 for value in key_horizons)

    by_category = _markout_summary(available, "category")
    worst_categories = sorted(by_category, key=lambda row: (row.get("mean_markout_cents") is None, row.get("mean_markout_cents") or 0))[:10]
    best_categories = sorted(by_category, key=lambda row: (row.get("mean_markout_cents") is not None, row.get("mean_markout_cents") or -999), reverse=True)[:10]

    adverse_patterns: List[Dict[str, Any]] = []
    for section, rows in {
        "by_spread_bucket": _markout_summary(available, "spread_bucket"),
        "by_depth_imbalance_bucket": _markout_summary(available, "depth_imbalance_bucket"),
        "by_price_bucket": _markout_summary(available, "price_bucket"),
        "by_time_to_close_bucket": _markout_summary(available, "time_to_close_bucket"),
    }.items():
        for row in rows:
            mean = _safe_float(row.get("mean_markout_cents"))
            if mean is not None and mean < 0 and int(row.get("sample_count") or 0) >= 3:
                adverse_patterns.append({"section": section, **row})

    blocker = "failed_negative_forward_markout" if all_key_negative else "needs_more_attribution"
    immediate_filters: List[str] = []
    for row in adverse_patterns:
        if row["section"] == "by_spread_bucket" and str(row["bucket"]).startswith(("spread_8", "spread_ge")):
            immediate_filters.append("tighten_max_spread")
        if row["section"] == "by_depth_imbalance_bucket" and "neg" in str(row["bucket"]):
            immediate_filters.append("do_not_follow_negative_imbalance_until_inverse_test")
        if row["section"] == "by_price_bucket" and str(row["bucket"]) in {"price_lt_10c", "price_ge_90c"}:
            immediate_filters.append("exclude_extreme_prices_for_taker")
    immediate_filters = sorted(set(immediate_filters))

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "taker_fill_count": len(fill_rows),
        "available_markout_count": len(available),
        "mean_markout_by_horizon": horizons,
        "by_policy": _markout_summary(available, "policy_id"),
        "by_category": by_category,
        "by_spread_bucket": _markout_summary(available, "spread_bucket"),
        "by_depth_imbalance_bucket": _markout_summary(available, "depth_imbalance_bucket"),
        "by_price_bucket": _markout_summary(available, "price_bucket"),
        "by_time_to_close_bucket": _markout_summary(available, "time_to_close_bucket"),
        "worst_categories": worst_categories,
        "best_categories": best_categories,
        "adverse_selection_patterns": adverse_patterns[:25],
        "immediate_filter_recommendations": immediate_filters,
        "answers": {
            "loss_concentrated_by_category": _concentration_answer(worst_categories, len(available)),
            "loss_concentrated_in_large_spread": any(row.get("section") == "by_spread_bucket" and str(row.get("bucket")).startswith(("spread_8", "spread_ge")) for row in adverse_patterns),
            "loss_concentrated_by_imbalance_direction": any(row.get("section") == "by_depth_imbalance_bucket" for row in adverse_patterns),
            "follow_imbalance_systematically_negative": all_key_negative,
        },
        "microstructure_taker_status": blocker,
        "conclusion": blocker,
    }


def _concentration_answer(worst_categories: List[Dict[str, Any]], total: int) -> Dict[str, Any]:
    if not worst_categories or total <= 0:
        return {"concentrated": False, "reason": "no_available_markout"}
    top = worst_categories[0]
    share = float(top.get("sample_count") or 0) / float(total)
    return {
        "concentrated": share >= 0.35,
        "top_category": top.get("bucket"),
        "top_category_sample_share": round(share, 6),
        "top_category_mean_markout_cents": top.get("mean_markout_cents"),
    }


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
    "build_microstructure_taker_failure_attribution",
    "load_jsonl",
    "write_json",
]
