#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_quality_surface import (  # noqa: E402
    build_quality_surface_report,
    dump_quality_surface_report,
)
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize quality-surface Polymarket weather paper evidence.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--quarantine-journal-dir", default=str(DEFAULT_QUARANTINE_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--allowed-side", action="append", dest="allowed_sides")
    parser.add_argument("--exclude-bucket-type", action="append", dest="excluded_bucket_types")
    parser.add_argument("--min-price", type=float, default=0.03)
    parser.add_argument("--max-price", type=float, default=0.85)
    parser.add_argument("--max-spread", type=float, default=0.02)
    parser.add_argument("--min-liquidity", type=float, default=10.0)
    parser.add_argument("--min-bid-depth-usdc-3c", type=float, default=10.0)
    parser.add_argument("--min-ask-depth-usdc-3c", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_quality_surface_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        backfill_dir=args.backfill_dir,
        allowed_sides=args.allowed_sides or (),
        excluded_bucket_types=args.excluded_bucket_types or ("eq",),
        min_price=args.min_price,
        max_price=args.max_price,
        max_spread=args.max_spread,
        min_liquidity=args.min_liquidity,
        min_bid_depth_usdc_3c=args.min_bid_depth_usdc_3c,
        min_ask_depth_usdc_3c=args.min_ask_depth_usdc_3c,
    )
    print(dump_quality_surface_report(report))


if __name__ == "__main__":
    main()
