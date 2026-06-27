#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_backfill import (  # noqa: E402
    DEFAULT_BACKFILL_DIR,
    dump_backfill_result,
    run_closed_weather_backfill,
)
from src.trading.polymarket_readonly import DEFAULT_WEATHER_QUERIES  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill resolved Polymarket weather markets as non-live-gate evidence.",
    )
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--polymarket-query", action="append", dest="polymarket_queries")
    parser.add_argument("--polymarket-row-limit", type=int, default=100)
    parser.add_argument("--polymarket-search-limit", type=int, default=25)
    parser.add_argument("--polymarket-city-search-limit", type=int, default=5)
    parser.add_argument("--max-city-temperature-queries", type=int, default=None)
    parser.add_argument("--no-city-temperature-queries", action="store_true")
    parser.add_argument("--max-records", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    result = run_closed_weather_backfill(
        queries=args.polymarket_queries or DEFAULT_WEATHER_QUERIES,
        row_limit=max(1, int(args.polymarket_row_limit)),
        search_limit_per_query=max(1, int(args.polymarket_search_limit)),
        include_city_temperature_queries=not bool(args.no_city_temperature_queries),
        city_search_limit_per_query=max(1, int(args.polymarket_city_search_limit)),
        max_city_temperature_queries=(
            max(0, int(args.max_city_temperature_queries))
            if args.max_city_temperature_queries is not None
            else None
        ),
        backfill_dir=args.backfill_dir,
        max_records=args.max_records,
    )
    print(dump_backfill_result(result))


if __name__ == "__main__":
    main()
