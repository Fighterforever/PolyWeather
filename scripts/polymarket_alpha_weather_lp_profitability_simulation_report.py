#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_profitability_simulator import (  # noqa: E402
    build_weather_lp_profitability_simulation,
    load_json,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Weather LP profitability scenario simulation.")
    parser.add_argument("--paper-quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--reward-risk-report", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--reward-share-report", default="evidence/weather_lp_rewards/reward_share_estimator_report.json")
    parser.add_argument("--reward-dollarization-report", default="evidence/weather_lp_rewards/reward_dollarization_report.json")
    parser.add_argument("--reward-dollarization-rows", default="evidence/weather_lp_rewards/reward_dollarization_rows.jsonl")
    parser.add_argument("--reward-allocation-audit-report", default="evidence/weather_lp_rewards/reward_allocation_audit_report.json")
    parser.add_argument("--weather-lp-experiment-report", default="evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    parser.add_argument("--rows-output", default="evidence/weather_lp_rewards/profitability_simulation_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/profitability_simulation_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_profitability_simulation(
        paper_quotes=load_jsonl(args.paper_quotes),
        quote_updates=load_jsonl(args.quote_updates),
        reward_vs_risk_report=load_json(args.reward_risk_report),
        reward_share_estimator_report=load_json(args.reward_share_report),
        reward_dollarization_report=load_json(args.reward_dollarization_report),
        reward_allocation_audit_report=load_json(args.reward_allocation_audit_report),
        weather_lp_experiment_report=load_json(args.weather_lp_experiment_report),
        reward_dollarization_rows=load_jsonl(args.reward_dollarization_rows),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    compact = {key: value for key, value in report.items() if key != "rows"}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "quote_count": compact.get("quote_count"),
                "conservative_net": (compact.get("scenario_table") or {}).get("conservative_net"),
                "base_net": (compact.get("scenario_table") or {}).get("base_net"),
                "optimistic_net": (compact.get("scenario_table") or {}).get("optimistic_net"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
