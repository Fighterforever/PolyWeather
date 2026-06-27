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
from src.trading.weather_quality_surface import (  # noqa: E402
    build_quarantine_promotion_report,
    dump_quality_surface_report,
)
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR  # noqa: E402


def _load_signal_report(path: str | None) -> dict | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("signal_report"), dict):
        return payload["signal_report"]
    return payload if isinstance(payload, dict) else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank quarantined Polymarket weather signals for formal-paper promotion review.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--quarantine-journal-dir", default=str(DEFAULT_QUARANTINE_JOURNAL_DIR))
    parser.add_argument(
        "--signal-report",
        help="Optional signal report JSON, or full paper-cycle JSON containing a signal_report object.",
    )
    parser.add_argument("--min-marked-count", type=int, default=5)
    parser.add_argument("--min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-maker-markout-count", type=int, default=3)
    parser.add_argument("--min-maker-mean-markout-cents", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_quarantine_promotion_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        signal_report=_load_signal_report(args.signal_report),
        min_marked_count=args.min_marked_count,
        min_mean_markout_cents=args.min_mean_markout_cents,
        min_win_rate=args.min_win_rate,
        min_maker_markout_count=args.min_maker_markout_count,
        min_maker_mean_markout_cents=args.min_maker_mean_markout_cents,
    )
    print(dump_quality_surface_report(report))


if __name__ == "__main__":
    main()
