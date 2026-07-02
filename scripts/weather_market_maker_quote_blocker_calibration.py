#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_maker_quote_blocker_calibration import (  # noqa: E402
    build_maker_quote_blocker_calibration_report,
    dump_maker_quote_blocker_calibration_report,
)
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402


def _load_signal_report(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("signal_report"), dict):
        return payload["signal_report"]
    if isinstance(payload, dict):
        return payload
    raise ValueError("signal report JSON must be an object")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate current maker-quote risk blockers against paper maker quote markouts.",
    )
    parser.add_argument("--signal-report-json", required=True)
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument(
        "--all-observations",
        action="store_true",
        help="Use every maker quote markout observation instead of only the latest markout per quote.",
    )
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--min-inferred-fills", type=int, default=3)
    parser.add_argument("--min-fill-inference-rate", type=float, default=0.05)
    parser.add_argument("--min-mean-maker-markout-cents", type=float, default=0.0)
    parser.add_argument("--min-maker-win-rate", type=float, default=0.55)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_maker_quote_blocker_calibration_report(
        _load_signal_report(args.signal_report_json),
        journal_dir=args.paper_journal_dir,
        latest_only=not bool(args.all_observations),
        min_count=args.min_count,
        min_inferred_fills=args.min_inferred_fills,
        min_fill_inference_rate=args.min_fill_inference_rate,
        min_mean_maker_markout_cents=args.min_mean_maker_markout_cents,
        min_maker_win_rate=args.min_maker_win_rate,
    )
    print(dump_maker_quote_blocker_calibration_report(report))


if __name__ == "__main__":
    main()
