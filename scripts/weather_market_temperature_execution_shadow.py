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
    summarize_maker_quote_strata,
)
from src.trading.weather_temperature_execution_experiment import (  # noqa: E402
    DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    build_temperature_execution_shadow_validation_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mark and summarize paper-only temperature execution shadow quotes "
            "generated from negative-maker opportunities."
        ),
    )
    parser.add_argument("--journal-dir", default=str(DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR))
    parser.add_argument("--markout-max-quotes", type=int, default=None)
    parser.add_argument("--min-markout-interval-seconds", type=float, default=600.0)
    parser.add_argument("--validation-min-quote-markouts", type=int, default=5)
    parser.add_argument("--validation-min-inferred-fills", type=int, default=3)
    parser.add_argument("--validation-min-fill-inference-rate", type=float, default=0.05)
    parser.add_argument("--validation-min-mean-maker-markout-cents", type=float, default=0.0)
    parser.add_argument("--validation-min-maker-win-rate", type=float, default=0.55)
    parser.add_argument("--validation-min-missed-taker-markout-cents", type=float, default=0.0)
    parser.add_argument(
        "--all-observations",
        action="store_true",
        help="Use every markout observation in strata instead of only the latest markout per quote.",
    )
    parser.add_argument("--min-count", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    markout = markout_open_maker_quotes(
        journal_dir=args.journal_dir,
        max_quotes=args.markout_max_quotes,
        min_markout_interval_seconds=float(args.min_markout_interval_seconds),
    )
    summary = summarize_maker_quote_journal(args.journal_dir)
    strata = summarize_maker_quote_strata(
        args.journal_dir,
        latest_only=not bool(args.all_observations),
        min_count=max(1, int(args.min_count)),
    )
    validation = build_temperature_execution_shadow_validation_report(
        journal_dir=args.journal_dir,
        min_quote_markouts=args.validation_min_quote_markouts,
        min_inferred_fills=args.validation_min_inferred_fills,
        min_fill_inference_rate=args.validation_min_fill_inference_rate,
        min_mean_maker_markout_cents=args.validation_min_mean_maker_markout_cents,
        min_maker_win_rate=args.validation_min_maker_win_rate,
        min_missed_taker_markout_cents=args.validation_min_missed_taker_markout_cents,
    )
    print(
        dump_maker_quote_summary(
            {
                "paper_only": True,
                "counts_for_live_gate": False,
                "journal_dir": args.journal_dir,
                "markout": markout,
                "summary": summary,
                "validation": validation,
                "strategy_strata": strata.get("by_quote_strategy") or [],
                "strategy_spread_strata": strata.get("by_strategy_and_spread") or [],
                "strategy_time_spread_strata": strata.get("by_strategy_and_fine_time_to_expiry_and_spread") or [],
                "do_not_live_rules": strata.get("do_not_live_rules") or [],
            }
        )
    )


if __name__ == "__main__":
    main()
