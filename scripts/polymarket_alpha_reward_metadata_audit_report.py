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
    audit_weather_reward_metadata,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Polymarket liquidity reward metadata for weather markets.")
    parser.add_argument("--markets", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/reward_metadata_audit_report.json")
    parser.add_argument("--rows-output", default="evidence/weather_lp_rewards/reward_metadata_rows.jsonl")
    parser.add_argument("--raw-samples-output", default="evidence/weather_lp_rewards/reward_metadata_raw_samples.jsonl")
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--fetch", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = audit_weather_reward_metadata(
        markets=load_jsonl(args.markets),
        fetch=bool(args.fetch),
        max_markets=int(args.max_markets or 0),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    write_jsonl(args.raw_samples_output, report.get("raw_samples") or [])
    compact = {k: v for k, v in report.items() if k not in {"rows", "raw_samples"}}
    compact["artifact_paths"] = {
        "reward_metadata_rows": str(args.rows_output),
        "reward_metadata_raw_samples": str(args.raw_samples_output),
    }
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "reward_metadata_available_count": compact.get("reward_metadata_available_count"),
                "min_max_incentive_found_count": compact.get("min_max_incentive_found_count"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
