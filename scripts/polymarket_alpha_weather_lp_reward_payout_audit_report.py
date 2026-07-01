#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_reward_payout_audit import (  # noqa: E402
    build_weather_lp_reward_payout_audit_report,
    load_csv,
    load_json,
    load_jsonl,
    render_manual_template_markdown,
    write_json,
    write_manual_template,
    write_text,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Weather LP reward payout audit template/report.")
    parser.add_argument("--reward-dollarization-report", default="evidence/weather_lp_rewards/reward_dollarization_report.json")
    parser.add_argument("--profitability-simulation-report", default="evidence/weather_lp_rewards/profitability_simulation_report.json")
    parser.add_argument("--paper-quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--reward-allocation-audit-report", default="evidence/weather_lp_rewards/reward_allocation_audit_report.json")
    parser.add_argument("--payout-csv", default="")
    parser.add_argument("--manual-template-output", default="evidence/weather_lp_rewards/reward_payout_manual_template.csv")
    parser.add_argument("--manual-template-md-output", default="evidence/weather_lp_rewards/reward_payout_manual_template.md")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/reward_payout_audit_report.json")
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    quote_rows = load_jsonl(args.paper_quotes)
    report = build_weather_lp_reward_payout_audit_report(
        reward_dollarization_report=load_json(args.reward_dollarization_report),
        profitability_simulation_report=load_json(args.profitability_simulation_report),
        paper_quotes=quote_rows,
        quote_updates=load_jsonl(args.quote_updates),
        reward_allocation_audit_report=load_json(args.reward_allocation_audit_report),
        payout_rows=load_csv(args.payout_csv) if args.payout_csv else [],
        generated_at=args.generated_at,
    )
    write_manual_template(args.manual_template_output, quote_rows, report)
    write_text(args.manual_template_md_output, render_manual_template_markdown(report, quote_rows))
    write_json(args.summary_output, report)
    print(json.dumps({"audit_status": report.get("audit_status"), "payout_gap_reason": report.get("payout_gap_reason"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
