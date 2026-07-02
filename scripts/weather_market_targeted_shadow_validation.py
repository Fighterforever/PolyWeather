#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_targeted_shadow import (  # noqa: E402
    DEFAULT_TARGETED_SHADOW_JOURNAL_DIR,
    build_targeted_shadow_validation_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate targeted-shadow Polymarket weather evidence before formal-paper promotion.",
    )
    parser.add_argument("--journal-dir", default=str(DEFAULT_TARGETED_SHADOW_JOURNAL_DIR))
    parser.add_argument("--min-marked-count", type=int, default=5)
    parser.add_argument("--min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-maker-inferred-fills", type=int, default=3)
    parser.add_argument("--min-maker-mean-markout-cents", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_targeted_shadow_validation_report(
        journal_dir=args.journal_dir,
        min_marked_count=args.min_marked_count,
        min_mean_markout_cents=args.min_mean_markout_cents,
        min_win_rate=args.min_win_rate,
        min_maker_inferred_fills=args.min_maker_inferred_fills,
        min_maker_mean_markout_cents=args.min_maker_mean_markout_cents,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
