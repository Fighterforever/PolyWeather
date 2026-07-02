#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_kill_switch_policy import (  # noqa: E402
    build_weather_lp_kill_switch_policy_report,
    load_json,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Weather LP kill-switch policy report.")
    parser.add_argument("--reward-risk-report", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--profitability-simulation-report", default="evidence/weather_lp_rewards/profitability_simulation_report.json")
    parser.add_argument("--position-sizing-report", default="evidence/weather_lp_rewards/position_sizing_report.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/kill_switch_policy_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_kill_switch_policy_report(
        reward_risk_report=load_json(args.reward_risk_report),
        profitability_simulation_report=load_json(args.profitability_simulation_report),
        position_sizing_report=load_json(args.position_sizing_report),
    )
    write_json(args.summary_output, report)
    print(json.dumps({"recommended_action": report.get("recommended_action"), "missing_controls": report.get("missing_controls"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
