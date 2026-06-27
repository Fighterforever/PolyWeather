#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_paper_journal import (  # noqa: E402
    DEFAULT_PAPER_JOURNAL_DIR,
    summarize_markout_strata,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize paper weather markouts by horizon and market quality buckets.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument(
        "--all-observations",
        action="store_true",
        help="Use every markout observation instead of only the latest markout per fill.",
    )
    parser.add_argument("--min-count", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    summary = summarize_markout_strata(
        args.paper_journal_dir,
        latest_only=not bool(args.all_observations),
        min_count=args.min_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
