#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_cancellation_policy import (  # noqa: E402
    build_weather_lp_cancellation_policy_report,
    load_jsonl,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate paper-only weather LP cancellation policies.")
    parser.add_argument("--quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/cancellation_policy_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_cancellation_policy_report(
        quotes=load_jsonl(args.quotes),
        quote_updates=load_jsonl(args.quote_updates),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "quote_count": report.get("quote_count"),
                "cancellation_policy_recommendation": report.get("cancellation_policy_recommendation"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
