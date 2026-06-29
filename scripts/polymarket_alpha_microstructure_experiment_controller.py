#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.microstructure_policy_sweep import load_json, load_jsonl, write_json  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_experiment_report.json"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_utc(value: Any):
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


def _first_time(rows: Iterable[Dict[str, Any]]) -> Optional[str]:
    values = [
        parsed
        for parsed in (_parse_utc(row.get("entry_time") or row.get("recorded_at") or row.get("timestamp")) for row in rows if isinstance(row, dict))
        if parsed is not None
    ]
    if not values:
        return None
    return min(values).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _horizon_mean(markout_report: Dict[str, Any], horizon: int) -> Optional[float]:
    for row in markout_report.get("mean_markout_by_horizon") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("bucket")) == str(horizon):
            return _safe_float(row.get("mean_markout_cents"))
    return None


def _top_policy(markout_report: Dict[str, Any], sweep_report: Dict[str, Any]) -> Optional[str]:
    rows = [row for row in markout_report.get("by_policy") or [] if isinstance(row, dict) and row.get("mean_markout_cents") is not None]
    if rows:
        return str(max(rows, key=lambda row: float(row.get("mean_markout_cents") or -1e9)).get("bucket"))
    policy_rows = [row for row in sweep_report.get("by_policy") or [] if isinstance(row, dict)]
    if policy_rows:
        return str(max(policy_rows, key=lambda row: int(row.get("candidate_count") or 0)).get("policy_id"))
    return None


def build_microstructure_experiment_report(
    *,
    watch_rows: list[Dict[str, Any]],
    policy_sweep_report: Dict[str, Any],
    taker_fills: list[Dict[str, Any]],
    maker_quotes: list[Dict[str, Any]],
    markout_report: Dict[str, Any],
    previous_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    previous_report = previous_report or {}
    taker_fill_count = len(taker_fills)
    maker_quote_count = len(maker_quotes)
    maker_inferred_fill_count = len([row for row in maker_quotes if row.get("inferred_fill")])
    mean_5m = _horizon_mean(markout_report, 300)
    mean_15m = _horizon_mean(markout_report, 900)
    mean_1h = _horizon_mean(markout_report, 3600)
    if taker_fill_count >= 30 and mean_1h is not None and mean_1h > 0:
        recommendation = "promote_policy_to_focused_paper"
    elif taker_fill_count >= 30 and mean_1h is not None and mean_1h < 0:
        recommendation = "pause_taker_microstructure"
    elif maker_quote_count >= 30 and maker_inferred_fill_count == 0:
        recommendation = "maker_quotes_not_getting_filled"
    elif maker_inferred_fill_count > 0 and mean_1h is not None and mean_1h < 0:
        recommendation = "pause_maker_microstructure"
    elif taker_fill_count == 0 and maker_quote_count > 0:
        recommendation = "keep_maker_shadow_only"
    else:
        recommendation = "continue_collecting"
    run_count = _safe_int(previous_report.get("run_count")) + 1 if previous_report else 1
    return {
        "schema_version": "polyweather_polymarket_alpha_microstructure_experiment.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "experiment_start": previous_report.get("experiment_start") or _first_time(watch_rows + taker_fills + maker_quotes),
        "run_count": run_count,
        "watch_count": len(watch_rows),
        "policy_candidate_count": _safe_int(policy_sweep_report.get("policy_candidate_count")),
        "taker_fill_count": taker_fill_count,
        "maker_quote_count": maker_quote_count,
        "maker_inferred_fill_count": maker_inferred_fill_count,
        "valid_markout_count": _safe_int(markout_report.get("available_markout_count")),
        "mean_5m_markout": mean_5m,
        "mean_15m_markout": mean_15m,
        "mean_1h_markout": mean_1h,
        "top_policy": _top_policy(markout_report, policy_sweep_report),
        "recommendation": recommendation,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only microstructure experiment controller report.")
    parser.add_argument("--watch-rows", default=str(DEFAULT_ROOT / "microstructure_watch_rows.jsonl"))
    parser.add_argument("--policy-sweep-report", default=str(DEFAULT_ROOT / "microstructure_policy_sweep_report.json"))
    parser.add_argument("--taker-fills", default=str(DEFAULT_ROOT / "microstructure_paper_fills.jsonl"))
    parser.add_argument("--maker-quotes", default=str(DEFAULT_ROOT / "microstructure_maker_quotes.jsonl"))
    parser.add_argument("--markout-report", default=str(DEFAULT_ROOT / "microstructure_markout_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_microstructure_experiment_report(
        watch_rows=load_jsonl(args.watch_rows),
        policy_sweep_report=load_json(args.policy_sweep_report),
        taker_fills=load_jsonl(args.taker_fills),
        maker_quotes=load_jsonl(args.maker_quotes),
        markout_report=load_json(args.markout_report),
        previous_report=load_json(args.summary_output),
    )
    report["artifact_paths"] = {
        "watch_rows": str(args.watch_rows),
        "policy_sweep_report": str(args.policy_sweep_report),
        "taker_fills": str(args.taker_fills),
        "maker_quotes": str(args.maker_quotes),
        "markout_report": str(args.markout_report),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "watch_count": report.get("watch_count"),
                "policy_candidate_count": report.get("policy_candidate_count"),
                "taker_fill_count": report.get("taker_fill_count"),
                "maker_quote_count": report.get("maker_quote_count"),
                "valid_markout_count": report.get("valid_markout_count"),
                "top_policy": report.get("top_policy"),
                "recommendation": report.get("recommendation"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
