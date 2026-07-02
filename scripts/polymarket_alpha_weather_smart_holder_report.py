#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_smart_holder_signal import build_weather_smart_holder_signal, load_jsonl, write_json, write_jsonl  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="evidence/weather_lp_rewards/lp_reward_markets.jsonl")
    parser.add_argument("--holder-rows", default="evidence/weather_lp_rewards/holder_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/smart_holder_signal_report.json")
    parser.add_argument("--signals-output", default="evidence/weather_lp_rewards/smart_holder_signals.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_smart_holder_signal(markets=load_jsonl(args.markets), holder_rows=load_jsonl(args.holder_rows))
    write_jsonl(args.signals_output, report.get("signals") or [])
    compact = {k: v for k, v in report.items() if k != "signals"}
    compact["artifact_paths"] = {"smart_holder_signals": str(args.signals_output)}
    write_json(args.summary_output, compact)
    print(json.dumps({"smart_holder_signal_count": compact.get("smart_holder_signal_count"), "gap_count": compact.get("gap_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
