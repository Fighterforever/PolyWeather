#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_live_readiness import (  # noqa: E402
    DEFAULT_BACKFILL_DIR,
    build_live_readiness_report,
    dump_readiness_report,
    load_signal_report,
)
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize paper evidence and live readiness for weather market trading.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument(
        "--signal-report",
        default=None,
        help="Optional JSON report from weather_market_signal_report.py for current signal availability.",
    )
    parser.add_argument(
        "--live-permission",
        action="store_true",
        help="Mark explicit live permission as present. This does not submit orders.",
    )
    parser.add_argument(
        "--include-quarantine-surface",
        action="store_true",
        help="Attach compact paper-only quarantine surface diagnostics without counting them toward the live gate.",
    )
    parser.add_argument(
        "--include-temperature-taker-validation",
        action="store_true",
        help="Attach paper-only formal taker validation diagnostics without counting them toward the live gate.",
    )
    parser.add_argument("--temperature-taker-journal-dir", default="data/trading/weather_temperature_taker_paper")
    parser.add_argument("--quarantine-journal-dir", default="data/trading/weather_quarantine_paper")
    parser.add_argument("--quarantine-surface-min-decision-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-promote-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--quarantine-surface-min-win-rate", type=float, default=0.55)
    parser.add_argument("--quarantine-surface-min-maker-quote-count", type=int, default=0)
    parser.add_argument("--quarantine-surface-min-maker-mean-markout-cents", type=float, default=0.0)
    parser.add_argument(
        "--settlement-grace-hours",
        type=float,
        default=24.0,
        help="Hours after market end to wait before treating unresolved fills as overdue.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_live_readiness_report(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        signal_report=load_signal_report(args.signal_report),
        include_quarantine_surface=bool(args.include_quarantine_surface),
        include_temperature_taker_validation=bool(args.include_temperature_taker_validation),
        temperature_taker_journal_dir=args.temperature_taker_journal_dir,
        live_permission=bool(args.live_permission),
        settlement_grace_hours=float(args.settlement_grace_hours),
        quarantine_surface_min_decision_count=args.quarantine_surface_min_decision_count,
        quarantine_surface_min_promote_count=args.quarantine_surface_min_promote_count,
        quarantine_surface_min_mean_markout_cents=args.quarantine_surface_min_mean_markout_cents,
        quarantine_surface_min_win_rate=args.quarantine_surface_min_win_rate,
        quarantine_surface_min_maker_quote_count=args.quarantine_surface_min_maker_quote_count,
        quarantine_surface_min_maker_mean_markout_cents=args.quarantine_surface_min_maker_mean_markout_cents,
    )
    print(dump_readiness_report(report))


if __name__ == "__main__":
    main()
