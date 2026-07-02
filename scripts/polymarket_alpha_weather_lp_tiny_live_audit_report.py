#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_tiny_live_audit import (  # noqa: E402
    build_weather_lp_tiny_live_audit_report,
    load_jsonl,
    write_json,
    write_tiny_live_payout_manual_template,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Weather LP tiny-live order lifecycle and payout.")
    parser.add_argument("--orders", default="evidence/weather_lp_rewards/tiny_live_orders.jsonl")
    parser.add_argument("--updates", default="evidence/weather_lp_rewards/tiny_live_order_updates.jsonl")
    parser.add_argument("--cancellations", default="evidence/weather_lp_rewards/tiny_live_cancellations.jsonl")
    parser.add_argument("--payout-rows", default="evidence/weather_lp_rewards/tiny_live_payout_filled.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/tiny_live_audit_report.json")
    parser.add_argument("--manual-template-output", default="evidence/weather_lp_rewards/tiny_live_payout_manual_template.csv")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    orders = load_jsonl(args.orders)
    report = build_weather_lp_tiny_live_audit_report(
        order_rows=orders,
        update_rows=load_jsonl(args.updates),
        cancellation_rows=load_jsonl(args.cancellations),
        payout_rows=load_jsonl(args.payout_rows),
    )
    write_json(args.summary_output, report)
    write_tiny_live_payout_manual_template(args.manual_template_output, orders)
    print(json.dumps({"placed_order_count": report.get("placed_order_count"), "payout_observed": report.get("payout_observed"), "live_order_path": report.get("live_order_path")}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
