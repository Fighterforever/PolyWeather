#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_historical_alpha_dataset import (  # noqa: E402
    build_weather_historical_alpha_dataset_from_paths,
    write_historical_alpha_dataset_artifacts,
)


DEFAULT_CLOSED_MARKETS = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_OBSERVATIONS = Path("evidence/official_observations/metar_intraday_history.jsonl")
DEFAULT_FORECASTS = Path("evidence/historical_forecasts/open_meteo_forecasts.jsonl")
DEFAULT_PRICES = Path("evidence/historical_markets/polymarket_price_history.jsonl")
DEFAULT_OUTPUT = Path("evidence/historical_alpha/weather_alpha_dataset.jsonl")
DEFAULT_MANIFEST = Path("evidence/historical_alpha/weather_alpha_dataset_manifest.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only weather historical alpha dataset.")
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED_MARKETS))
    parser.add_argument("--intraday-observations", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--forecasts", default=str(DEFAULT_FORECASTS))
    parser.add_argument("--price-history", default=str(DEFAULT_PRICES))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_historical_alpha_dataset_from_paths(
        closed_markets_path=args.closed_markets,
        intraday_observations_path=args.intraday_observations,
        forecasts_path=args.forecasts,
        price_history_path=args.price_history,
    )
    manifest = write_historical_alpha_dataset_artifacts(report, output_path=args.output, manifest_path=args.manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
