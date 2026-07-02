#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_maker_quote_journal import (  # noqa: E402
    markout_open_maker_quotes,
    summarize_maker_quote_journal,
    write_maker_quote_journal_from_fills,
)
from src.trading.weather_paper_journal import markout_open_paper_fills  # noqa: E402
from src.trading.weather_price_conditioned_snapshot_backfill import (  # noqa: E402
    DEFAULT_PRICE_CONDITIONED_SNAPSHOT_BACKFILL_DIR,
    dump_snapshot_backfill_report,
    replay_price_conditioned_snapshots_to_journal,
)
from src.trading.weather_quality_surface import (  # noqa: E402
    DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR,
    build_price_conditioned_validation_report,
)
from src.trading.weather_resolved_audit import audit_paper_fills_resolution  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover missed price-conditioned paper entries from recorded snapshots.",
    )
    parser.add_argument("--source-journal-dir", default=str(DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR))
    parser.add_argument("--target-journal-dir", default=str(DEFAULT_PRICE_CONDITIONED_SNAPSHOT_BACKFILL_DIR))
    parser.add_argument("--sample-interval-minutes", type=float, default=60.0)
    parser.add_argument("--max-snapshots", type=int)
    parser.add_argument("--profile", default="price-conditioned-snapshot-backfill")
    parser.add_argument("--markout", action="store_true")
    parser.add_argument("--markout-max-fills", type=int, default=100)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--audit-max-fills", type=int, default=100)
    parser.add_argument("--write-maker-quotes", action="store_true")
    parser.add_argument("--maker-quote-size", type=float, default=1.0)
    parser.add_argument("--maker-max-quotes", type=int, default=100)
    parser.add_argument("--maker-markout-max-quotes", type=int, default=100)
    parser.add_argument("--validate", action="store_true")
    return parser.parse_args(argv)


def _without_records(payload: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if payload is None:
        return None
    return {key: value for key, value in payload.items() if key != "records"}


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    backfill = replay_price_conditioned_snapshots_to_journal(
        source_journal_dir=args.source_journal_dir,
        target_journal_dir=args.target_journal_dir,
        sample_interval_minutes=args.sample_interval_minutes,
        max_snapshots=args.max_snapshots,
        profile=args.profile,
    )
    markout = (
        markout_open_paper_fills(
            journal_dir=args.target_journal_dir,
            max_fills=args.markout_max_fills,
        )
        if bool(args.markout)
        else None
    )
    resolved_audit = (
        audit_paper_fills_resolution(
            journal_dir=args.target_journal_dir,
            max_fills=args.audit_max_fills,
            include_unresolved=True,
        )
        if bool(args.audit)
        else None
    )
    maker_quote_journal = None
    maker_quote_markout = None
    maker_quote_summary = None
    if bool(args.write_maker_quotes):
        maker_quote_journal = write_maker_quote_journal_from_fills(
            journal_dir=args.target_journal_dir,
            quote_size=float(args.maker_quote_size),
            max_quotes=args.maker_max_quotes,
        )
        maker_quote_markout = markout_open_maker_quotes(
            journal_dir=args.target_journal_dir,
            max_quotes=args.maker_markout_max_quotes,
        )
        maker_quote_summary = summarize_maker_quote_journal(args.target_journal_dir)
    validation = (
        build_price_conditioned_validation_report(journal_dir=args.target_journal_dir)
        if bool(args.validate)
        else None
    )
    return {
        "schema_version": backfill["schema_version"],
        "paper_only": True,
        "counts_for_live_gate": False,
        "backfill": {key: value for key, value in backfill.items() if key != "writes"},
        "markout": _without_records(markout),
        "resolved_audit": _without_records(resolved_audit),
        "maker_quote_journal": maker_quote_journal,
        "maker_quote_markout": maker_quote_markout,
        "maker_quote_summary": maker_quote_summary,
        "validation": validation,
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    print(dump_snapshot_backfill_report(build_report(args)))


if __name__ == "__main__":
    main()
