#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_quarantine_validation import (  # noqa: E402
    DEFAULT_QUARANTINE_JOURNAL_DIR,
    build_quarantine_validation_report,
    dump_quarantine_validation_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate formal weather-market risk rules against quarantine paper evidence.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--quarantine-journal-dir", default=str(DEFAULT_QUARANTINE_JOURNAL_DIR))
    parser.add_argument("--min-unlock-count", type=int, default=5)
    parser.add_argument("--min-maker-inferred-fills", type=int, default=3)
    parser.add_argument("--min-unlock-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--min-unlock-win-rate", type=float, default=0.55)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_quarantine_validation_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        min_unlock_count=args.min_unlock_count,
        min_maker_inferred_fills=args.min_maker_inferred_fills,
        min_unlock_mean_markout_cents=args.min_unlock_mean_markout_cents,
        min_unlock_win_rate=args.min_unlock_win_rate,
    )
    print(dump_quarantine_validation_report(report))


if __name__ == "__main__":
    main()
