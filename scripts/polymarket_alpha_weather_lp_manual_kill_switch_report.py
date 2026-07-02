#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_manual_kill_switch import (  # noqa: E402
    build_weather_lp_manual_kill_switch_checklist,
    load_json,
    render_markdown,
    write_json,
    write_text,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build manual Weather LP kill-switch checklist.")
    parser.add_argument("--position-sizing-report", default="evidence/weather_lp_rewards/position_sizing_report.json")
    parser.add_argument("--kill-switch-policy-report", default="evidence/weather_lp_rewards/kill_switch_policy_report.json")
    parser.add_argument("--operator-confirmed", action="store_true")
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--markdown-output", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.md")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_manual_kill_switch_checklist(
        position_sizing_report=load_json(args.position_sizing_report),
        kill_switch_policy_report=load_json(args.kill_switch_policy_report),
        operator_confirmed=args.operator_confirmed,
    )
    write_json(args.json_output, report)
    write_text(args.markdown_output, render_markdown(report))
    print(json.dumps({"checklist_status": report.get("checklist_status"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
