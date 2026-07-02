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
from src.trading.weather_resolved_audit import (  # noqa: E402
    build_resolved_gap_report,
    dump_summary,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Explain unresolved Polymarket weather paper fills by due date, audit status, and backfill coverage.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--settlement-grace-hours", type=float, default=24.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_resolved_gap_report(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        settlement_grace_hours=args.settlement_grace_hours,
    )
    print(dump_summary(report))


if __name__ == "__main__":
    main()
