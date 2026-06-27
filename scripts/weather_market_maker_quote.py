#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_maker_quote_journal import (  # noqa: E402
    dump_maker_quote_summary,
    markout_open_maker_quotes,
    summarize_maker_quote_journal,
    write_maker_quote_journal_from_fills,
)
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and mark paper-only maker-bid shadow quotes for weather market fills.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--quote-size", type=float, default=1.0)
    parser.add_argument(
        "--maker-quote-offset-cents",
        action="append",
        type=float,
        dest="maker_quote_offset_cents",
        help="Add a paper maker quote this many cents below entry bid. Repeat for a ladder; default is 0.",
    )
    parser.add_argument("--max-quotes", type=int, default=None)
    parser.add_argument("--markout-max-quotes", type=int, default=None)
    parser.add_argument("--min-markout-interval-seconds", type=float, default=0.0)
    parser.add_argument(
        "--markout-only",
        action="store_true",
        help="Skip creating new maker quotes and only mark existing open quotes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    quote_result = None
    if not bool(args.markout_only):
        quote_result = write_maker_quote_journal_from_fills(
            journal_dir=args.paper_journal_dir,
            quote_size=float(args.quote_size),
            quote_offset_cents=args.maker_quote_offset_cents,
            max_quotes=args.max_quotes,
        )
    markout_result = markout_open_maker_quotes(
        journal_dir=args.paper_journal_dir,
        max_quotes=args.markout_max_quotes,
        min_markout_interval_seconds=float(args.min_markout_interval_seconds),
    )
    summary = summarize_maker_quote_journal(args.paper_journal_dir)
    print(
        dump_maker_quote_summary(
            {
                "quote_write": quote_result,
                "markout": markout_result,
                "summary": summary,
            }
        )
    )


if __name__ == "__main__":
    main()
