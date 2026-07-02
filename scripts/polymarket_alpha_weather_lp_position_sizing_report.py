#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_dataset import write_jsonl  # noqa: E402
from src.trading.polymarket_alpha.weather_lp_position_sizing import (  # noqa: E402
    build_weather_lp_position_sizing_report,
    load_jsonl,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Weather LP position sizing report.")
    parser.add_argument("--paper-quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--profitability-rows", default="evidence/weather_lp_rewards/profitability_simulation_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/position_sizing_report.json")
    parser.add_argument("--rows-output", default="evidence/weather_lp_rewards/position_sizing_rows.jsonl")
    parser.add_argument("--per-market-risk-cap-dollars", type=float, default=10.0)
    parser.add_argument("--per-city-exposure-cap-dollars", type=float, default=25.0)
    parser.add_argument("--total-weather-lp-exposure-cap-dollars", type=float, default=100.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_position_sizing_report(
        quotes=load_jsonl(args.paper_quotes),
        profitability_rows=load_jsonl(args.profitability_rows),
        per_market_risk_cap_dollars=args.per_market_risk_cap_dollars,
        per_city_exposure_cap_dollars=args.per_city_exposure_cap_dollars,
        total_weather_lp_exposure_cap_dollars=args.total_weather_lp_exposure_cap_dollars,
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    compact = {key: value for key, value in report.items() if key != "rows"}
    write_json(args.summary_output, compact)
    print(json.dumps({"quote_count": compact.get("quote_count"), "recommended_total_capital_at_risk": compact.get("recommended_total_capital_at_risk"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
