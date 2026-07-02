#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_quality_surface import (  # noqa: E402
    build_quality_threshold_calibration_report,
    dump_quality_surface_report,
)
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR  # noqa: E402


def _float_list(values: list[str] | None, default: tuple[float, ...]) -> tuple[float, ...]:
    if not values:
        return default
    return tuple(float(value) for value in values)


def _bucket_exclusion_sets(values: list[str] | None) -> list[tuple[str, ...]] | None:
    if not values:
        return None
    output: list[tuple[str, ...]] = []
    for value in values:
        parts = tuple(
            sorted(
                {
                    item.strip().lower()
                    for item in str(value or "").split(",")
                    if item.strip()
                }
            )
        )
        output.append(parts)
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grid-search Polymarket weather paper evidence thresholds without changing live gate.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--quarantine-journal-dir", default=str(DEFAULT_QUARANTINE_JOURNAL_DIR))
    parser.add_argument(
        "--bucket-type-exclusion-set",
        action="append",
        dest="bucket_type_exclusion_sets",
        help="Comma-separated excluded bucket types for one grid row, e.g. eq or eq,range. Repeat to test multiple sets.",
    )
    parser.add_argument("--min-price", action="append", dest="min_price_options")
    parser.add_argument("--max-spread", action="append", dest="max_spread_options")
    parser.add_argument("--min-liquidity", action="append", dest="min_liquidity_options")
    parser.add_argument("--min-depth", action="append", dest="min_depth_options")
    parser.add_argument("--max-price", type=float, default=0.85)
    parser.add_argument("--min-marked-count", type=int, default=10)
    parser.add_argument("--min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_quality_threshold_calibration_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        bucket_type_exclusion_sets=_bucket_exclusion_sets(args.bucket_type_exclusion_sets),
        min_price_options=_float_list(args.min_price_options, (0.001, 0.03, 0.05, 0.10)),
        max_spread_options=_float_list(args.max_spread_options, (0.005, 0.01, 0.02, 0.03)),
        min_liquidity_options=_float_list(args.min_liquidity_options, (0.0, 10.0, 50.0)),
        min_depth_options=_float_list(args.min_depth_options, (0.0, 10.0, 50.0)),
        max_price=args.max_price,
        min_marked_count=args.min_marked_count,
        min_mean_markout_cents=args.min_mean_markout_cents,
        min_win_rate=args.min_win_rate,
    )
    print(dump_quality_surface_report(report))


if __name__ == "__main__":
    main()
