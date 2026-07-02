#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_temperature_execution_experiment import (  # noqa: E402
    DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR,
    run_temperature_taker_paper_cycle,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run paper-only formal taker probes for temperature execution opportunities.",
    )
    parser.add_argument("--execution-journal-dir", default=str(DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR))
    parser.add_argument("--taker-journal-dir", default=str(DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR))
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--max-fills", type=int, default=20)
    parser.add_argument("--markout-max-fills", type=int, default=20)
    parser.add_argument("--min-reentry-seconds", type=int, default=3600)
    parser.add_argument("--min-markout-interval-seconds", type=float, default=600.0)
    parser.add_argument("--validation-min-marked-count", type=int, default=10)
    parser.add_argument("--validation-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--validation-min-win-rate", type=float, default=0.55)
    parser.add_argument("--validation-required-horizon", action="append", dest="validation_required_horizons")
    parser.add_argument("--validation-min-horizon-count", type=int, default=3)
    parser.add_argument("--validation-min-resolved-count", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = run_temperature_taker_paper_cycle(
        execution_journal_dir=args.execution_journal_dir,
        taker_journal_dir=args.taker_journal_dir,
        max_items=args.max_items,
        max_fills=args.max_fills,
        markout_max_fills=args.markout_max_fills,
        min_reentry_seconds=args.min_reentry_seconds,
        min_markout_interval_seconds=float(args.min_markout_interval_seconds),
        validation_min_marked_count=args.validation_min_marked_count,
        validation_min_mean_markout_cents=args.validation_min_mean_markout_cents,
        validation_min_win_rate=args.validation_min_win_rate,
        validation_required_horizons=args.validation_required_horizons or ("0-5m", "5-15m", "15-30m"),
        validation_min_horizon_count=args.validation_min_horizon_count,
        validation_min_resolved_count=args.validation_min_resolved_count,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
