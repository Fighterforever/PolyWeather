#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_tiny_live_gap import (  # noqa: E402
    build_weather_lp_tiny_live_gap_report,
    load_json,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Weather LP tiny-live gap report without enabling live.")
    parser.add_argument("--profitability-simulation-report", default="evidence/weather_lp_rewards/profitability_simulation_report.json")
    parser.add_argument("--weather-lp-experiment-report", default="evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    parser.add_argument("--reward-risk-report", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--position-sizing-report", default="evidence/weather_lp_rewards/position_sizing_report.json")
    parser.add_argument("--kill-switch-policy-report", default="evidence/weather_lp_rewards/kill_switch_policy_report.json")
    parser.add_argument("--reward-payout-audit-report", default="evidence/weather_lp_rewards/reward_payout_audit_report.json")
    parser.add_argument("--manual-order-sheet-report", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet_report.json")
    parser.add_argument("--impact-simulator-report", default="evidence/weather_lp_rewards/tiny_live_impact_simulator_report.json")
    parser.add_argument("--manual-kill-switch-checklist", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--manual-payout-audit-plan-report", default="evidence/weather_lp_rewards/manual_tiny_live_payout_audit_plan.json")
    parser.add_argument("--selected-audit-quotes-report", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.json")
    parser.add_argument("--manual-payout-audit-result", default="evidence/weather_lp_rewards/manual_payout_audit_result.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/weather_lp_tiny_live_gap_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_tiny_live_gap_report(
        profitability_simulation_report=load_json(args.profitability_simulation_report),
        weather_lp_experiment_report=load_json(args.weather_lp_experiment_report),
        reward_risk_report=load_json(args.reward_risk_report),
        position_sizing_report=load_json(args.position_sizing_report),
        kill_switch_policy_report=load_json(args.kill_switch_policy_report),
        reward_payout_audit_report=load_json(args.reward_payout_audit_report),
        manual_order_sheet_report=load_json(args.manual_order_sheet_report),
        impact_simulator_report=load_json(args.impact_simulator_report),
        manual_kill_switch_checklist=load_json(args.manual_kill_switch_checklist),
        manual_payout_audit_plan_report=load_json(args.manual_payout_audit_plan_report),
        selected_audit_quotes_report=load_json(args.selected_audit_quotes_report),
        manual_payout_audit_result=load_json(args.manual_payout_audit_result),
    )
    write_json(args.summary_output, report)
    print(json.dumps({"current_status": report["current_status"], "reason": report["tiny_live_not_allowed_reason"], "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
