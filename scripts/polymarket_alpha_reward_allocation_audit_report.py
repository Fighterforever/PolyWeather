#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.polymarket_reward_metadata import (  # noqa: E402
    audit_reward_allocation_conversion,
    load_jsonl,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit whether LP reward points can be converted to estimated cents.")
    parser.add_argument("--reward-metadata-rows", default="evidence/weather_lp_rewards/reward_metadata_rows.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/reward_allocation_audit_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = audit_reward_allocation_conversion(
        reward_metadata_rows=load_jsonl(args.reward_metadata_rows),
        quote_updates=load_jsonl(args.quote_updates),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "market_count": report.get("market_count"),
                "reward_allocation_available_count": report.get("reward_allocation_available_count"),
                "estimated_reward_cents_available_count": report.get("estimated_reward_cents_available_count"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
