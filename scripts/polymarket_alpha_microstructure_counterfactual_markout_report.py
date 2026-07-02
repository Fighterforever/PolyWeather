#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.microstructure_policy_sweep import (  # noqa: E402
    build_microstructure_policy_markout_report,
    build_microstructure_policy_sweep,
    load_json,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_inverse_counterfactual_markout_report.json"
DEFAULT_MARKOUTS = DEFAULT_ROOT / "microstructure_inverse_counterfactual_markouts.jsonl"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "microstructure_policy_candidates_v2.jsonl"


def _mean_from_report(report: dict, horizon: int) -> float | None:
    for row in report.get("mean_markout_by_horizon") or []:
        if str(row.get("bucket")) == str(horizon):
            value = row.get("mean_markout_cents")
            return float(value) if value is not None else None
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute counterfactual markout for inverse microstructure taker policy.")
    parser.add_argument("--watch-rows", default=str(DEFAULT_ROOT / "microstructure_watch_rows.jsonl"))
    parser.add_argument("--orderbook-snapshots", default=str(DEFAULT_ROOT / "microstructure_orderbook_snapshots.jsonl"))
    parser.add_argument("--followup-orderbook-snapshots", default=str(DEFAULT_ROOT / "microstructure_followup_orderbook_snapshots.jsonl"))
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--follow-markout-report", default=str(DEFAULT_ROOT / "microstructure_markout_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--markouts-output", default=str(DEFAULT_MARKOUTS))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    sweep = build_microstructure_policy_sweep(
        watch_rows=load_jsonl(args.watch_rows),
        orderbook_snapshots=load_jsonl(args.orderbook_snapshots),
        active_markets=load_jsonl(args.active_markets),
        include_inverse=True,
        include_baseline=True,
    )
    inverse_candidates = [row for row in sweep.get("candidates") or [] if row.get("policy_id") == "taker_inverse_imbalance"]
    inverse_fills = [row for row in sweep.get("taker_fills") or [] if row.get("policy_id") == "taker_inverse_imbalance"]
    inverse_report = build_microstructure_policy_markout_report(
        taker_fills=inverse_fills,
        maker_quotes=[],
        orderbook_snapshots=load_jsonl(args.orderbook_snapshots) + load_jsonl(args.followup_orderbook_snapshots),
    )
    follow_report = load_json(args.follow_markout_report)
    inverse_5m = _mean_from_report(inverse_report, 300)
    follow_5m = _mean_from_report(follow_report, 300)
    inverse_15m = _mean_from_report(inverse_report, 900)
    inverse_1h = _mean_from_report(inverse_report, 3600)
    if inverse_report.get("available_markout_count", 0) and inverse_1h is not None and inverse_1h > 0:
        decision = "microstructure_taker_direction_maybe_inverted"
    elif inverse_report.get("available_markout_count", 0):
        decision = "pause_taker_microstructure_entirely"
    else:
        decision = "inverse_unavailable_missing_counterfactual_snapshots"
    compact = {
        "schema_version": "polyweather_polymarket_alpha_microstructure_inverse_counterfactual.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "inverse_candidate_count": len(inverse_candidates),
        "inverse_available_markout_count": inverse_report.get("available_markout_count"),
        "inverse_mean_5m_markout": inverse_5m,
        "inverse_mean_15m_markout": inverse_15m,
        "inverse_mean_1h_markout": inverse_1h,
        "compare_to_follow": {
            "follow_mean_5m": follow_5m,
            "inverse_mean_5m": inverse_5m,
            "delta": round(inverse_5m - follow_5m, 8) if inverse_5m is not None and follow_5m is not None else None,
        },
        "by_category": inverse_report.get("by_category"),
        "by_depth_imbalance_bucket": [],
        "decision": decision,
        "artifact_paths": {"markouts": str(args.markouts_output), "policy_candidates_v2": str(args.candidates_output)},
    }
    write_jsonl(args.candidates_output, sweep.get("candidates") or [])
    write_jsonl(args.markouts_output, inverse_report.get("markouts") or [])
    write_json(args.summary_output, compact)
    print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
