#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_manual_payout_audit_plan import load_json, write_json, write_text  # noqa: E402


def build_manual_execution_checklist(
    *,
    selected_quotes_report: Dict[str, Any],
    manual_payout_audit_plan: Dict[str, Any],
    manual_kill_switch_checklist: Dict[str, Any],
) -> Dict[str, Any]:
    selected = selected_quotes_report.get("selected_quotes") or []
    return {
        "schema_version": "polyweather_polymarket_alpha_weather_lp_manual_execution_checklist.v1",
        "selected_quote_count": len(selected),
        "before_placing": [
            "confirm live_order_path=false",
            "confirm AI/bot will not place orders",
            "confirm user manually opens Polymarket UI",
            "confirm selected market slug",
            "confirm token/outcome",
            "confirm quote price",
            "confirm quote size",
            "confirm order is resting, not crossing",
            "confirm min_incentive_size",
            "confirm max_incentive_spread",
            "confirm visible reward estimate",
            "confirm max loss if filled",
            "confirm cancel rules",
        ],
        "during_test": [
            "record start_time_utc",
            "record quote visible on orderbook",
            "record whether reward metadata still available",
            "record midpoint / best bid / best ask every 5 minutes",
            "cancel if reward disqualified",
            "cancel if markout loss exceeds threshold",
            "cancel if hour boundary rule triggers",
            "cancel if user cannot monitor",
        ],
        "after_test": [
            "record cancel_time_utc",
            "record whether quote was filled",
            "record actual fill if any",
            "record markout",
            "record reward payout after next UTC midnight / epoch",
            "fill payout audit template",
        ],
        "selected_quotes": selected,
        "cancel_rules": manual_payout_audit_plan.get("cancel_rules") or {},
        "manual_kill_switch_ready": bool(manual_kill_switch_checklist.get("ready")),
        "manual_execution_only": True,
        "manual_review_required": True,
        "no_api_order_placement": True,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Weather LP Manual Execution Checklist",
        "",
        "Manual UI only. AI/bot must not place or cancel orders.",
        "",
        "## Before Placing",
    ]
    for item in report.get("before_placing") or []:
        lines.append(f"- [ ] {item}")
    lines.append("")
    lines.append("## During Test")
    for item in report.get("during_test") or []:
        lines.append(f"- [ ] {item}")
    lines.append("")
    lines.append("## After Test")
    for item in report.get("after_test") or []:
        lines.append(f"- [ ] {item}")
    lines.append("")
    lines.append("## Selected Quotes")
    for row in report.get("selected_quotes") or []:
        lines.append(
            f"- rank={row.get('rank')} market={row.get('market_slug')} side={row.get('side')} "
            f"price={row.get('suggested_quote_price')} size={row.get('suggested_quote_size')} "
            f"capital_at_risk={row.get('capital_at_risk')}"
        )
    lines.extend(["", "no_api_order_placement=true", "live_order_path=false"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build human-readable manual Weather LP execution checklist.")
    parser.add_argument("--selected-quotes-report", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.json")
    parser.add_argument("--manual-payout-audit-plan", default="evidence/weather_lp_rewards/manual_tiny_live_payout_audit_plan.json")
    parser.add_argument("--manual-kill-switch-checklist", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/manual_execution_checklist.json")
    parser.add_argument("--markdown-output", default="evidence/weather_lp_rewards/manual_execution_checklist.md")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_manual_execution_checklist(
        selected_quotes_report=load_json(args.selected_quotes_report),
        manual_payout_audit_plan=load_json(args.manual_payout_audit_plan),
        manual_kill_switch_checklist=load_json(args.manual_kill_switch_checklist),
    )
    write_json(args.json_output, report)
    write_text(args.markdown_output, render_markdown(report))
    print(json.dumps({"selected_quote_count": report.get("selected_quote_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
