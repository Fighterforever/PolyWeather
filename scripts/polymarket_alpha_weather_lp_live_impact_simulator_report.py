#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_live_impact_simulator import (  # noqa: E402
    build_weather_lp_live_impact_simulator_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate manual tiny-live quote impact without execution.")
    parser.add_argument("--manual-order-rows", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet.json")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--rows-output", default="evidence/weather_lp_rewards/tiny_live_impact_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/tiny_live_impact_simulator_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_live_impact_simulator_report(
        manual_order_rows=load_jsonl(args.manual_order_rows),
        quote_updates=load_jsonl(args.quote_updates),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    compact = {key: value for key, value in report.items() if key != "rows"}
    write_json(args.summary_output, compact)
    print(json.dumps({"manual_quote_count": compact.get("manual_quote_count"), "impact_simulation_ready": compact.get("impact_simulation_ready"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
