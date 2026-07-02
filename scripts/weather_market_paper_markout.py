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
    markout_open_paper_fills,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record paper-only markouts for open weather market paper fills.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--max-fills", type=int, default=None)
    parser.add_argument("--min-markout-interval-seconds", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    result = markout_open_paper_fills(
        journal_dir=args.paper_journal_dir,
        max_fills=args.max_fills,
        min_markout_interval_seconds=args.min_markout_interval_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
