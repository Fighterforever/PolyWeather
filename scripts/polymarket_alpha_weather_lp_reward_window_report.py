#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_reward_window import build_weather_lp_reward_window_report, load_jsonl, write_json  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", default="evidence/weather_lp_rewards/reward_window_observations.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/reward_window_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_reward_window_report(load_jsonl(args.observations))
    write_json(args.summary_output, report)
    print(json.dumps({"observations_count": report.get("observations_count"), "support_40_51": report.get("40_to_51_minute_support_count"), "confidence": report.get("confidence"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
