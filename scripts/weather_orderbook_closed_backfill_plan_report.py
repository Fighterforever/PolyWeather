#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR  # noqa: E402
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements  # noqa: E402
from src.trading.weather_orderbook_archive_coverage import build_orderbook_closed_token_coverage_report  # noqa: E402
from src.trading.weather_paper_journal import load_jsonl  # noqa: E402


def _summary_only(report: dict) -> dict:
    trimmed = dict(report)
    trimmed.pop("matched_samples", None)
    trimmed.pop("closed_markets_missing_archive_samples", None)
    trimmed.pop("archived_markets_pending_closed_backfill_samples", None)
    trimmed.pop("closed_missing_token_samples", None)
    followup = dict(trimmed.get("closed_backfill_followup_plan") or {})
    followup.pop("requests", None)
    followup.pop("await_market_end_samples", None)
    followup.pop("missing_end_time_samples", None)
    followup.pop("next_await_market_queries", None)
    trimmed["closed_backfill_followup_plan"] = followup
    return trimmed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a paper-only plan for refreshing closed backfill from archived "
            "pre-resolution weather orderbooks."
        ),
    )
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ORDERBOOK_ARCHIVE_DIR))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--max-samples", type=int, default=20)
    parser.add_argument("--summary-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    backfill_dir = Path(args.backfill_dir)
    orderbook_dir = Path(args.orderbook_archive_dir)
    report = build_orderbook_closed_token_coverage_report(
        closed_records=load_closed_backfill_records_with_snapshot_supplements(backfill_dir),
        orderbook_snapshots=load_jsonl(orderbook_dir / "orderbook_snapshots.jsonl"),
        generated_at=args.generated_at,
        max_samples=max(0, int(args.max_samples)),
    )
    if args.summary_only:
        report = _summary_only(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
