#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_manual_order_sheet import (  # noqa: E402
    load_jsonl,
    select_manual_payout_audit_quotes,
    write_json,
)


def _write_csv(path: str | Path, rows: list[dict]) -> int:
    columns = [
        "rank",
        "market_slug",
        "city",
        "token_id",
        "side",
        "suggested_quote_price",
        "suggested_quote_size",
        "capital_at_risk",
        "min_incentive_size",
        "max_incentive_spread",
        "distance_from_midpoint",
        "visible_reward_share_proxy",
        "expected_reward_low",
        "expected_reward_base",
        "expected_reward_high",
        "current_markout",
        "cancel_rules",
        "manual_execution_only",
        "live_order_path",
    ]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select up to three manual Weather LP payout-audit quotes.")
    parser.add_argument("--manual-order-sheet-json", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet.json")
    parser.add_argument("--impact-rows", default="evidence/weather_lp_rewards/tiny_live_impact_rows.jsonl")
    parser.add_argument("--profitability-rows", default="evidence/weather_lp_rewards/profitability_simulation_rows.jsonl")
    parser.add_argument("--max-quote-count", type=int, default=3)
    parser.add_argument("--max-total-capital-at-risk", type=float, default=25.0)
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.json")
    parser.add_argument("--csv-output", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.csv")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = select_manual_payout_audit_quotes(
        manual_order_rows=load_jsonl(args.manual_order_sheet_json),
        impact_rows=load_jsonl(args.impact_rows),
        profitability_rows=load_jsonl(args.profitability_rows),
        max_quote_count=args.max_quote_count,
        max_total_capital_at_risk=args.max_total_capital_at_risk,
    )
    write_json(args.json_output, report)
    _write_csv(args.csv_output, report.get("selected_quotes") or [])
    print(
        json.dumps(
            {
                "selected_quote_count": report.get("selected_quote_count"),
                "total_selected_capital_at_risk": report.get("total_selected_capital_at_risk"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
