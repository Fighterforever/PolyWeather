#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_reward_discovery import build_weather_lp_reward_discovery, load_json, write_json, write_jsonl  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family-catalog", default="evidence/bucket_family/weather_bucket_family_catalog.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/lp_reward_discovery_report.json")
    parser.add_argument("--markets-output", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_reward_discovery(family_catalog=load_json(args.family_catalog), generated_at=args.generated_at)
    write_jsonl(args.markets_output, report.get("markets") or [])
    compact = {k: v for k, v in report.items() if k != "markets"}
    compact["artifact_paths"] = {"lp_reward_markets": str(args.markets_output)}
    write_json(args.summary_output, compact)
    observation = {
        "generated_at": compact.get("generated_at"),
        "minute_of_hour": (report.get("markets") or [{}])[0].get("minute_of_hour") if report.get("markets") else None,
        "reward_available_count": compact.get("reward_available_count"),
        "reward_market_count": compact.get("reward_market_count"),
        "market_slugs": [row.get("market_slug") for row in (report.get("markets") or []) if row.get("reward_available")][:25],
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    obs_path = Path("evidence/weather_lp_rewards/reward_window_observations.jsonl")
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    with obs_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"reward_market_count": compact.get("reward_market_count"), "reward_metadata_missing_count": compact.get("reward_metadata_missing_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
