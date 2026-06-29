#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.active_probability_edge_scanner import load_json  # noqa: E402
from src.trading.polymarket_alpha.probability_edge_journal import load_jsonl, write_json  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


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


def _mean(values: Iterable[Any]) -> Optional[float]:
    parsed: List[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        parsed.append(number)
    return round(sum(parsed) / len(parsed), 8) if parsed else None


def _by_horizon(markout_report: Dict[str, Any], label: str) -> Optional[float]:
    aliases = {
        "60s": {"60", "60s"},
        "300s": {"300", "300s", "5m"},
        "5m": {"300", "300s", "5m"},
        "900s": {"900", "900s", "15m"},
        "15m": {"900", "900s", "15m"},
        "3600s": {"3600", "3600s", "1h"},
        "1h": {"3600", "3600s", "1h"},
        "21600s": {"21600", "21600s", "6h"},
        "6h": {"21600", "21600s", "6h"},
        "86400s": {"86400", "86400s", "24h"},
        "24h": {"86400", "86400s", "24h"},
        "current": {"0", "current"},
    }.get(label, {label})
    for row in markout_report.get("by_horizon") or []:
        if not isinstance(row, dict):
            continue
        row_label = str(row.get("bucket") if row.get("bucket") is not None else row.get("horizon"))
        if row_label in aliases:
            return row.get("mean") if row.get("mean") is not None else row.get("mean_markout_cents")
    return None


def _first_entry_time(rows: Iterable[Dict[str, Any]]) -> Optional[str]:
    times = [
        parsed
        for parsed in (_parse_utc(row.get("entry_time") or row.get("timestamp") or row.get("recorded_at")) for row in rows if isinstance(row, dict))
        if parsed is not None
    ]
    if not times:
        return None
    return min(times).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _identity(row: Dict[str, Any]) -> str:
    return "|".join(str(row.get(field) or "") for field in ("market_slug", "token_id", "side"))


def _surface_supported_fill_count(fills: List[Dict[str, Any]], surface_report: Dict[str, Any]) -> int:
    supported = {
        _identity(row)
        for row in (surface_report.get("rows") or []) + (surface_report.get("top_relative_value_rows") or [])
        if isinstance(row, dict) and row.get("surface_supports_model_direction") is True
    }
    return sum(1 for fill in fills if _identity(fill) in supported)


def _sensitivity_fragile_fill_count(fills: List[Dict[str, Any]], sensitivity_report: Dict[str, Any]) -> int:
    fragile = {
        _identity(row)
        for row in sensitivity_report.get("rows") or []
        if isinstance(row, dict) and row.get("sensitivity_fragile") is True
    }
    return sum(1 for fill in fills if _identity(fill) in fragile)


def build_crypto_touch_72h_experiment_report(
    *,
    crypto_probability_report: Dict[str, Any],
    formal_fills: List[Dict[str, Any]],
    markout_report: Dict[str, Any],
    near_miss_watch: List[Dict[str, Any]],
    near_miss_markout_report: Dict[str, Any],
    surface_report: Dict[str, Any],
    sensitivity_report: Dict[str, Any],
    active_probability_report: Dict[str, Any],
) -> Dict[str, Any]:
    formal_fill_count = len(formal_fills)
    formal_valid_markout_count = int(markout_report.get("available_markout_count") or 0)
    near_miss_watch_count = len(near_miss_watch)
    surface_supported_fill_count = _surface_supported_fill_count(formal_fills, surface_report)
    sensitivity_fragile_fill_count = _sensitivity_fragile_fill_count(formal_fills, sensitivity_report)
    vol_supported = {
        _identity(row)
        for row in sensitivity_report.get("rows") or []
        if isinstance(row, dict) and row.get("vol_supports_trade") is True
    }
    formal_fills_vol_supported_count = sum(1 for fill in formal_fills if _identity(fill) in vol_supported)
    surface_valid_group_count = int(surface_report.get("surface_valid_group_count") or 0)
    mean_1h = _by_horizon(markout_report, "3600s") or _by_horizon(markout_report, "1h")
    mean_6h = _by_horizon(markout_report, "21600s") or _by_horizon(markout_report, "6h")
    mean_current = _by_horizon(markout_report, "current")
    near_short_negative = any(
        value is not None and value < 0
        for value in (
            _by_horizon(near_miss_markout_report, "300s"),
            _by_horizon(near_miss_markout_report, "5m"),
            _by_horizon(near_miss_markout_report, "900s"),
            _by_horizon(near_miss_markout_report, "15m"),
            _by_horizon(near_miss_markout_report, "3600s"),
            _by_horizon(near_miss_markout_report, "1h"),
        )
    )
    all_formal_negative = bool(
        formal_fill_count > 0
        and formal_valid_markout_count > 0
        and all(value is not None and value < 0 for value in (mean_1h, mean_current) if value is not None)
    )
    if all_formal_negative and formal_fill_count > 0 and (
        sensitivity_fragile_fill_count >= formal_fill_count
        or surface_supported_fill_count == 0
        or formal_fills_vol_supported_count == 0
    ):
        recommendation = "shadow_only_pending_recalibration"
    elif formal_fill_count >= 20 and (mean_1h is not None and mean_1h < 0) and (mean_6h is not None and mean_6h < 0):
        recommendation = "pause_crypto_touch"
    elif all_formal_negative and (
        surface_supported_fill_count < formal_fill_count
        or (formal_fill_count > 0 and sensitivity_fragile_fill_count >= formal_fill_count)
    ):
        recommendation = "reduce_priority_or_pause_after_more_samples"
    else:
        recommendation = "continue_collecting"
    return {
        "schema_version": "polyweather_polymarket_alpha_crypto_touch_72h_experiment.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "experiment_start": _first_entry_time(formal_fills + near_miss_watch),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "run_count": 1,
        "formal_fill_count": formal_fill_count,
        "formal_valid_markout_count": formal_valid_markout_count,
        "near_miss_watch_count": near_miss_watch_count,
        "surface_supported_fill_count": surface_supported_fill_count,
        "sensitivity_fragile_fill_count": sensitivity_fragile_fill_count,
        "mean_markout_1h": mean_1h,
        "mean_markout_6h": mean_6h,
        "mean_markout_current": mean_current,
        "near_miss_mean_markout_5m": _by_horizon(near_miss_markout_report, "300s") or _by_horizon(near_miss_markout_report, "5m"),
        "near_miss_mean_markout_15m": _by_horizon(near_miss_markout_report, "900s") or _by_horizon(near_miss_markout_report, "15m"),
        "formal_fill_mode": active_probability_report.get("formal_fill_mode") or "disabled",
        "new_formal_fill_generation_enabled": bool(active_probability_report.get("new_formal_fill_generation_enabled")),
        "surface_valid_group_count": surface_valid_group_count,
        "formal_fills_surface_supported_after_fix": surface_supported_fill_count,
        "formal_fills_vol_supported_count": formal_fills_vol_supported_count,
        "formal_fills_negative_markout_count": int(formal_fill_count if all_formal_negative else 0),
        "old_formal_fill_count": active_probability_report.get("old_formal_fill_count"),
        "new_formal_fill_count": active_probability_report.get("new_formal_fill_count"),
        "downgraded_due_surface_count": active_probability_report.get("downgraded_due_surface_count"),
        "downgraded_due_sensitivity_count": active_probability_report.get("downgraded_due_sensitivity_count"),
        "crypto_candidate_count": crypto_probability_report.get("candidate_count"),
        "recommendation": recommendation,
        "keep_threshold_do_not_lower": bool(near_short_negative),
        "promote_to_tiny_live_review_candidate_never_set_true_until_enough_resolved_markout": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only 72h controller report for crypto touch lane.")
    parser.add_argument("--crypto-report", default=str(DEFAULT_ROOT / "crypto_probability_edge_report.json"))
    parser.add_argument("--active-report", default=str(DEFAULT_ROOT / "active_probability_edge_report.json"))
    parser.add_argument("--formal-fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--markout-report", default=str(DEFAULT_PAPER / "markout_report.json"))
    parser.add_argument("--near-miss-watch", default=str(DEFAULT_ROOT / "crypto_touch_near_miss_watch.jsonl"))
    parser.add_argument("--near-miss-markout-report", default=str(DEFAULT_ROOT / "crypto_touch_near_miss_markout_report.json"))
    parser.add_argument("--surface-report", default=str(DEFAULT_ROOT / "crypto_touch_surface_report.json"))
    parser.add_argument("--sensitivity-report", default=str(DEFAULT_ROOT / "crypto_touch_sensitivity_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_touch_72h_experiment_report.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_crypto_touch_72h_experiment_report(
        crypto_probability_report=load_json(args.crypto_report),
        formal_fills=load_jsonl(args.formal_fills),
        markout_report=load_json(args.markout_report),
        near_miss_watch=load_jsonl(args.near_miss_watch),
        near_miss_markout_report=load_json(args.near_miss_markout_report),
        surface_report=load_json(args.surface_report),
        sensitivity_report=load_json(args.sensitivity_report),
        active_probability_report=load_json(args.active_report),
    )
    report["artifact_paths"] = {
        "crypto_report": str(args.crypto_report),
        "active_report": str(args.active_report),
        "formal_fills": str(args.formal_fills),
        "markout_report": str(args.markout_report),
        "near_miss_watch": str(args.near_miss_watch),
        "near_miss_markout_report": str(args.near_miss_markout_report),
        "surface_report": str(args.surface_report),
        "sensitivity_report": str(args.sensitivity_report),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "formal_fill_count": report.get("formal_fill_count"),
                "formal_valid_markout_count": report.get("formal_valid_markout_count"),
                "recommendation": report.get("recommendation"),
                "keep_threshold_do_not_lower": report.get("keep_threshold_do_not_lower"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
