#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_manual_order_sheet import (  # noqa: E402
    build_weather_lp_manual_order_sheet,
    load_json,
    load_jsonl,
    write_csv,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build manual tiny-live review sheet without placing orders.")
    parser.add_argument("--quote-optimizer-report", default="evidence/weather_lp_rewards/quote_optimizer_report.json")
    parser.add_argument("--reward-risk-report", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--profitability-simulation-report", default="evidence/weather_lp_rewards/profitability_simulation_report.json")
    parser.add_argument("--position-sizing-report", default="evidence/weather_lp_rewards/position_sizing_report.json")
    parser.add_argument("--kill-switch-policy-report", default="evidence/weather_lp_rewards/kill_switch_policy_report.json")
    parser.add_argument("--paper-quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--profitability-rows", default="evidence/weather_lp_rewards/profitability_simulation_rows.jsonl")
    parser.add_argument("--position-sizing-rows", default="evidence/weather_lp_rewards/position_sizing_rows.jsonl")
    parser.add_argument("--csv-output", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet.csv")
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_manual_order_sheet(
        quote_optimizer_report=load_json(args.quote_optimizer_report),
        reward_vs_risk_report=load_json(args.reward_risk_report),
        profitability_simulation_report=load_json(args.profitability_simulation_report),
        position_sizing_report=load_json(args.position_sizing_report),
        kill_switch_policy_report=load_json(args.kill_switch_policy_report),
        paper_quotes=load_jsonl(args.paper_quotes),
        quote_updates=load_jsonl(args.quote_updates),
        profitability_rows=load_jsonl(args.profitability_rows),
        position_sizing_rows=load_jsonl(args.position_sizing_rows),
    )
    rows = report.get("rows") or []
    write_csv(args.csv_output, rows)
    Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_output).write_text(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    compact = {key: value for key, value in report.items() if key != "rows"}
    write_json(args.summary_output, compact)
    print(json.dumps({"manual_quote_count": compact.get("suggested_manual_quote_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
