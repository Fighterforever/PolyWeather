#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_tiny_live_selector import (  # noqa: E402
    build_weather_lp_tiny_live_selected_orders,
    load_jsonl_or_json,
    write_json,
    write_selected_orders_csv,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Weather LP tiny-live selected order candidates without placing orders.")
    parser.add_argument("--selected-quotes", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.json")
    parser.add_argument("--impact-rows", default="evidence/weather_lp_rewards/tiny_live_impact_rows.jsonl")
    parser.add_argument("--kill-switch", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/tiny_live_selected_orders.json")
    parser.add_argument("--csv-output", default="evidence/weather_lp_rewards/tiny_live_selected_orders.csv")
    return parser.parse_args(argv)


def _load_dict(path: str | Path) -> dict:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    selected_report = _load_dict(args.selected_quotes)
    report = build_weather_lp_tiny_live_selected_orders(
        selected_quote_report=selected_report,
        impact_rows=load_jsonl_or_json(args.impact_rows),
        kill_switch_checklist=_load_dict(args.kill_switch),
        env=os.environ,
    )
    write_json(args.json_output, report)
    write_selected_orders_csv(args.csv_output, report.get("selected_orders") or [])
    print(
        json.dumps(
            {
                "selected_order_count": report.get("selected_order_count"),
                "selected_total_capital_at_risk": report.get("selected_total_capital_at_risk"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
