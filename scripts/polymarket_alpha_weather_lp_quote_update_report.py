#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_paper_journal import (  # noqa: E402
    build_weather_lp_quote_update_ledger,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update paper-only weather LP quote ledger.")
    parser.add_argument("--quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--reward-markets", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/paper_quote_update_report.json")
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_quote_update_ledger(
        quotes=load_jsonl(args.quotes),
        reward_markets=load_jsonl(args.reward_markets),
        existing_updates=load_jsonl(args.quote_updates),
        generated_at=args.generated_at,
    )
    write_jsonl(args.quote_updates, report.get("quote_updates") or [])
    compact = {key: value for key, value in report.items() if key not in {"new_updates", "quote_updates"}}
    compact["artifact_paths"] = {"quote_updates": str(args.quote_updates)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "active_quote_count": compact.get("active_quote_count"),
                "quote_update_count": compact.get("quote_update_count"),
                "new_update_count": compact.get("new_update_count"),
                "cumulative_reward_points_proxy": compact.get("cumulative_reward_points_proxy"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
