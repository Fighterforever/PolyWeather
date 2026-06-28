#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_market_backfill_bulk import load_closed_weather_markets  # noqa: E402
from src.weather.historical_forecast_backfill import (  # noqa: E402
    backfill_open_meteo_historical_forecasts,
    write_forecast_artifacts,
)


DEFAULT_CLOSED_MARKETS = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_OUTPUT = Path("evidence/historical_forecasts/open_meteo_forecasts.jsonl")
DEFAULT_MANIFEST = Path("evidence/historical_forecasts/open_meteo_forecast_manifest.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill paper-only Open-Meteo historical forecast features.")
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED_MARKETS))
    parser.add_argument("--model", action="append", dest="models", default=None)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--timeout", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    closed = load_closed_weather_markets(args.closed_markets)
    station_dates = [
        {"station_code": row.get("station_code"), "target_date": row.get("target_date")}
        for row in closed
        if row.get("station_code") and row.get("target_date")
    ]
    report = backfill_open_meteo_historical_forecasts(
        station_dates=station_dates,
        models=args.models or ["gfs_seamless"],
        timeout=int(args.timeout),
    )
    manifest = write_forecast_artifacts(report, output_path=args.output, manifest_path=args.manifest)
    print(json.dumps({"manifest": manifest, "gap_count": report.get("gap_count")}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
