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
    build_weather_lp_reward_share_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate visible-orderbook Weather LP reward share proxy.")
    parser.add_argument("--quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--reward-markets", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--rows-output", default="evidence/weather_lp_rewards/reward_share_estimator_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/reward_share_estimator_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_reward_share_report(
        quotes=load_jsonl(args.quotes),
        reward_markets=load_jsonl(args.reward_markets),
        quote_updates=load_jsonl(args.quote_updates),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    compact = {key: value for key, value in report.items() if key != "rows"}
    compact["artifact_paths"] = {"rows": str(args.rows_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "visible_reward_share_median": compact.get("visible_reward_share_median"),
                "break_even_share_median": compact.get("break_even_share_median"),
                "quotes_where_visible_share_exceeds_break_even": compact.get("quotes_where_visible_share_exceeds_break_even"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
