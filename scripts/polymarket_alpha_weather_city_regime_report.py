#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_city_regime import build_weather_city_regime, load_json, load_jsonl, write_json, write_jsonl  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family-catalog", default="evidence/bucket_family/weather_bucket_family_catalog.json")
    parser.add_argument("--observations", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/city_regime_report.json")
    parser.add_argument("--table-output", default="evidence/weather_lp_rewards/city_regime_table.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_city_regime(family_catalog=load_json(args.family_catalog), observations=load_jsonl(args.observations))
    write_jsonl(args.table_output, report.get("city_regimes") or [])
    compact = {k: v for k, v in report.items() if k != "city_regimes"}
    compact["artifact_paths"] = {"city_regime_table": str(args.table_output)}
    write_json(args.summary_output, compact)
    print(json.dumps({"city_profile_count": compact.get("city_profile_count"), "sufficient_profile_count": compact.get("sufficient_profile_count"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
