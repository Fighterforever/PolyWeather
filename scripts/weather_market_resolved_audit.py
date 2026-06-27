#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_resolved_audit import (  # noqa: E402
    audit_paper_fills_resolution,
    compact_resolved_audit_log,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit paper weather market fills against resolved Polymarket outcomes.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--max-fills", type=int, default=None)
    parser.add_argument(
        "--resolved-only",
        action="store_true",
        help="Skip unresolved fills instead of recording unresolved audit rows.",
    )
    parser.add_argument(
        "--no-backfill-fallback",
        action="store_true",
        help="Disable closed-backfill lookup for fills whose live market lookup is still unresolved.",
    )
    parser.add_argument(
        "--compact-existing",
        action="store_true",
        help="Compact repeated unchanged rows in resolved_audits.jsonl instead of running a fresh audit.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write compaction changes. Without this flag, --compact-existing is a dry run.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a backup when writing compaction changes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.compact_existing:
        result = compact_resolved_audit_log(
            journal_dir=args.paper_journal_dir,
            write=bool(args.write),
            backup=not bool(args.no_backup),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    result = audit_paper_fills_resolution(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        max_fills=args.max_fills,
        include_unresolved=not bool(args.resolved_only),
        use_backfill_fallback=not bool(args.no_backfill_fallback),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
