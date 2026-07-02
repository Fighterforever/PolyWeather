#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_historical_price_backfill import (  # noqa: E402
    backfill_polymarket_price_history,
    write_price_history_artifacts,
)
from src.trading.weather_closed_market_backfill_bulk import load_closed_weather_markets  # noqa: E402


DEFAULT_CLOSED_MARKETS = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_OUTPUT = Path("evidence/historical_markets/polymarket_price_history.jsonl")
DEFAULT_GAP_REPORT = Path("evidence/historical_markets/polymarket_price_history_gap_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill paper-only Polymarket historical price data.")
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED_MARKETS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--gap-report", default=str(DEFAULT_GAP_REPORT))
    parser.add_argument("--interval", default="all")
    parser.add_argument("--fidelity", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = backfill_polymarket_price_history(
        load_closed_weather_markets(args.closed_markets),
        interval=args.interval,
        fidelity=int(args.fidelity),
        timeout=int(args.timeout),
        max_tokens=args.max_tokens,
    )
    gap_report = write_price_history_artifacts(report, output_path=args.output, gap_report_path=args.gap_report)
    print(json.dumps(gap_report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
