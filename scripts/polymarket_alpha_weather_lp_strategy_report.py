#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_strategy import build_weather_lp_strategy, load_jsonl, write_json, write_jsonl  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reward-markets", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--city-regimes", default="evidence/weather_lp_rewards/city_regime_table.jsonl")
    parser.add_argument("--smart-holder-signals", default="evidence/weather_lp_rewards/smart_holder_signals.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/weather_lp_strategy_report.json")
    parser.add_argument("--candidates-output", default="evidence/weather_lp_rewards/weather_lp_candidates.jsonl")
    parser.add_argument("--watch-output", default="evidence/weather_lp_rewards/weather_lp_watch_rows.jsonl")
    parser.add_argument("--rejected-output", default="evidence/weather_lp_rewards/weather_lp_rejected_rows.jsonl")
    parser.add_argument("--basket-risk-output", default="evidence/weather_lp_rewards/basket_risk_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_strategy(
        reward_markets=load_jsonl(args.reward_markets),
        city_regimes=load_jsonl(args.city_regimes),
        smart_holder_signals=load_jsonl(args.smart_holder_signals),
    )
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    write_jsonl(args.watch_output, report.get("watch_rows") or [])
    write_jsonl(args.rejected_output, report.get("rejected") or [])
    compact = {k: v for k, v in report.items() if k not in {"candidates", "watch_rows", "rejected"}}
    compact["rejected_count"] = len(report.get("rejected") or [])
    compact["artifact_paths"] = {"candidates": str(args.candidates_output), "watch_rows": str(args.watch_output), "rejected": str(args.rejected_output), "basket_risk": str(args.basket_risk_output)}
    risk_rows = []
    for row in (report.get("candidates") or []) + (report.get("watch_rows") or []) + (report.get("rejected") or []):
        risk_rows.append(
            {
                "market_slug": row.get("market_slug"),
                "strategy_id": row.get("strategy_id"),
                "total_entry_cost": row.get("basket_total_cost"),
                "basket_width": row.get("basket_width"),
                "decision": row.get("decision"),
                "blockers": row.get("blockers"),
                "expensive_basket_blocker": "expensive_basket_near_full_payout" if (row.get("basket_total_cost") or 0) >= 0.98 else None,
                "reward_counted_as_guaranteed_pnl": False,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    write_json(
        args.basket_risk_output,
        {
            "schema_version": "polyweather_polymarket_alpha_weather_lp_basket_risk.v1.report",
            "basket_count": len(risk_rows),
            "expensive_basket_rejection_count": len([row for row in risk_rows if row.get("expensive_basket_blocker")]),
            "reward_counted_as_guaranteed_pnl": False,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "baskets": risk_rows,
        },
    )
    write_json(args.summary_output, compact)
    print(json.dumps({"candidate_count": compact.get("candidate_count"), "watch_count": compact.get("watch_count"), "reject_count": compact.get("reject_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
