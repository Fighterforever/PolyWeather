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
    build_manual_payout_audit_result,
    load_csv,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest user-filled manual Weather LP payout audit CSV.")
    parser.add_argument("--filled-csv", default="evidence/weather_lp_rewards/manual_payout_audit_filled.csv")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/manual_payout_audit_result.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    source = Path(args.filled_csv)
    report = build_manual_payout_audit_result(filled_rows=load_csv(source), filled_csv_exists=source.exists())
    write_json(args.summary_output, report)
    print(json.dumps({"audit_verdict": report.get("audit_verdict"), "audit_status": report.get("audit_status"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
