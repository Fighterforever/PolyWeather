#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_quarantine_surface import (  # noqa: E402
    build_quarantine_surface_report,
    dump_quarantine_surface_report,
)
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify quarantine weather-market evidence by bucket into promote, cooldown, or continue-sampling actions.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--quarantine-journal-dir", default=str(DEFAULT_QUARANTINE_JOURNAL_DIR))
    parser.add_argument("--min-decision-count", type=int, default=5)
    parser.add_argument("--min-promote-count", type=int, default=5)
    parser.add_argument("--min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-maker-quote-count", type=int, default=0)
    parser.add_argument("--min-maker-mean-markout-cents", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_quarantine_surface_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        min_decision_count=args.min_decision_count,
        min_promote_count=args.min_promote_count,
        min_mean_markout_cents=args.min_mean_markout_cents,
        min_win_rate=args.min_win_rate,
        min_maker_quote_count=args.min_maker_quote_count,
        min_maker_mean_markout_cents=args.min_maker_mean_markout_cents,
    )
    print(dump_quarantine_surface_report(report))


if __name__ == "__main__":
    main()
