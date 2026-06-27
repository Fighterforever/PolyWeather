#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements  # noqa: E402
from src.weather.official_value_backfill import build_official_value_backfill_report  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a paper-only official final value backfill report for closed weather markets.",
    )
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--max-gap-samples", type=int, default=10)
    parser.add_argument("--max-backfill-plan-requests", type=int, default=20)
    parser.add_argument("--max-records", type=int)
    parser.add_argument(
        "--fetch-external-official-values",
        "--fetch-wunderground",
        action="store_true",
        dest="fetch_external_official_values",
        help="Fetch supported external official history sources: Wunderground for Wunderground-settled markets and recent AviationWeather METAR for METAR-settled markets.",
    )
    parser.add_argument(
        "--allow-wunderground-proxy",
        action="store_true",
        help="Allow Wunderground to be used for non-Wunderground settlement sources. Diagnostic only; disabled by default.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Drop row-level supplements and gap samples from stdout.",
    )
    parser.add_argument(
        "--supplements-only",
        action="store_true",
        help="Print only ready supplements as JSONL for later replay-seed ingestion.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    records = load_closed_backfill_records_with_snapshot_supplements(args.backfill_dir)
    report = build_official_value_backfill_report(
        records,
        fetch_external=args.fetch_external_official_values,
        allow_wunderground_proxy=args.allow_wunderground_proxy,
        max_gap_samples=args.max_gap_samples,
        max_backfill_plan_requests=args.max_backfill_plan_requests,
        max_records=args.max_records,
    )
    if args.supplements_only:
        for row in report.get("supplements") or []:
            if row.get("status") == "ready":
                print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return
    if args.summary_only:
        report = dict(report)
        report.pop("supplements", None)
        report.pop("gap_samples", None)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
