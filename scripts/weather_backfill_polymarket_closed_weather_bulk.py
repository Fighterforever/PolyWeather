#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_market_backfill_bulk import (  # noqa: E402
    build_closed_weather_market_bulk_report,
    write_closed_weather_market_bulk_artifacts,
)


DEFAULT_OUTPUT = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_MANIFEST = Path("evidence/historical_markets/polymarket_closed_weather_manifest.json")
DEFAULT_GAP_REPORT = Path("evidence/historical_markets/polymarket_closed_weather_gap_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill paper-only Polymarket closed weather markets in bulk.")
    parser.add_argument("--event-limit", type=int, default=500)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--gap-report", default=str(DEFAULT_GAP_REPORT))
    parser.add_argument("--generated-at", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_closed_weather_market_bulk_report(
        event_limit=max(1, int(args.event_limit)),
        page_size=max(1, int(args.page_size)),
        generated_at=args.generated_at,
    )
    artifacts = write_closed_weather_market_bulk_artifacts(
        report,
        output_path=args.output,
        manifest_path=args.manifest,
        gap_report_path=args.gap_report,
    )
    print(json.dumps(artifacts, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
