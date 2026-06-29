#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_quote_optimizer import (  # noqa: E402
    build_weather_lp_quote_optimizer,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Weather LP quote optimizer report.")
    parser.add_argument("--reward-markets", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--city-regimes", default="evidence/weather_lp_rewards/city_regime_table.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/quote_optimizer_report.json")
    parser.add_argument("--candidates-output", default="evidence/weather_lp_rewards/quote_optimizer_candidates.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_quote_optimizer(
        reward_markets=load_jsonl(args.reward_markets),
        city_regimes=load_jsonl(args.city_regimes),
    )
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    compact = {key: value for key, value in report.items() if key not in {"candidates", "all_variants"}}
    compact["artifact_paths"] = {"candidates": str(args.candidates_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "market_count": compact.get("market_count"),
                "selected_quote_count": compact.get("selected_quote_count"),
                "reward_qualified_variant_count": compact.get("reward_qualified_variant_count"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
