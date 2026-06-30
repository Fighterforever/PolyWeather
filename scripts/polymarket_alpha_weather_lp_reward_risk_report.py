#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_reward_risk import (  # noqa: E402
    build_weather_lp_reward_risk_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only weather LP reward-vs-risk ledger.")
    parser.add_argument("--quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--measurement-cohorts", default="evidence/weather_lp_rewards/measurement_cohorts.jsonl")
    parser.add_argument("--city-regimes", default="evidence/weather_lp_rewards/city_regime_table.jsonl")
    parser.add_argument("--rows-output", default="evidence/weather_lp_rewards/reward_vs_risk_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--city-basket-output", default="evidence/weather_lp_rewards/city_basket_attribution_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_reward_risk_report(
        quotes=load_jsonl(args.quotes),
        quote_updates=load_jsonl(args.quote_updates),
        measurement_cohorts=load_jsonl(args.measurement_cohorts),
        city_regimes=load_jsonl(args.city_regimes),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    city_basket = report.get("city_basket_attribution") or {}
    compact = {key: value for key, value in report.items() if key not in {"rows", "quote_summaries", "city_basket_attribution"}}
    compact["artifact_paths"] = {"rows": str(args.rows_output), "city_basket_attribution": str(args.city_basket_output)}
    write_json(args.summary_output, compact)
    write_json(args.city_basket_output, city_basket)
    print(
        json.dumps(
            {
                "paper_quote_count": compact.get("paper_quote_count"),
                "quote_update_count": compact.get("quote_update_count"),
                "cumulative_reward_points_proxy": compact.get("cumulative_reward_points_proxy"),
                "mean_current_markout": compact.get("mean_current_markout"),
                "reward_to_risk_proxy": compact.get("reward_to_risk_proxy"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
