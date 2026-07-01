#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_manual_payout_audit_plan import (  # noqa: E402
    build_manual_payout_audit_plan,
    load_json,
    render_markdown,
    write_json,
    write_text,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a manual-only Weather LP payout audit plan.")
    parser.add_argument("--selected-quotes-report", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.json")
    parser.add_argument("--manual-kill-switch-checklist", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--reward-payout-audit-report", default="evidence/weather_lp_rewards/reward_payout_audit_report.json")
    parser.add_argument("--recommended-first-test-capital", type=float, default=25.0)
    parser.add_argument("--max-total-capital-at-risk", type=float, default=70.0)
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/manual_tiny_live_payout_audit_plan.json")
    parser.add_argument("--markdown-output", default="evidence/weather_lp_rewards/manual_tiny_live_payout_audit_plan.md")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_manual_payout_audit_plan(
        selected_quotes_report=load_json(args.selected_quotes_report),
        manual_kill_switch_checklist=load_json(args.manual_kill_switch_checklist),
        reward_payout_audit_report=load_json(args.reward_payout_audit_report),
        recommended_first_test_capital=args.recommended_first_test_capital,
        max_total_capital_at_risk=args.max_total_capital_at_risk,
    )
    write_json(args.json_output, report)
    write_text(args.markdown_output, render_markdown(report))
    print(json.dumps({"plan_status": report.get("plan_status"), "selected_quote_count": report.get("selected_quote_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
