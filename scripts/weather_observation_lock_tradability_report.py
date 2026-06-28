#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_observation_lock_tradability import (  # noqa: E402
    build_observation_lock_tradability_report_from_paths,
    write_observation_lock_tradability_artifacts,
)


DEFAULT_REPLAY = Path("evidence/historical_replay/observation_lock_historical_replay.json")
DEFAULT_PRICES = Path("evidence/historical_markets/polymarket_price_history.jsonl")
DEFAULT_DATASET = Path("evidence/historical_alpha/weather_alpha_dataset.jsonl")
DEFAULT_SUMMARY = Path("evidence/historical_replay/observation_lock_tradability_report.json")
DEFAULT_ROWS = Path("evidence/historical_replay/observation_lock_locked_signal_triage.jsonl")
DEFAULT_GE_LE_SUMMARY = Path("evidence/historical_replay/observation_lock_ge_le_tradability_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only tradability triage for historical observation-lock signals.")
    parser.add_argument("--historical-replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--price-history", default=str(DEFAULT_PRICES))
    parser.add_argument("--alpha-dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROWS))
    parser.add_argument("--ge-le-summary-output", default=str(DEFAULT_GE_LE_SUMMARY))
    parser.add_argument("--cost", type=float, default=0.005)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_observation_lock_tradability_report_from_paths(
        historical_replay_path=args.historical_replay,
        price_history_path=args.price_history,
        alpha_dataset_path=args.alpha_dataset,
        cost=float(args.cost),
    )
    write_observation_lock_tradability_artifacts(
        report,
        summary_output=args.summary_output,
        rows_output=args.rows_output,
        ge_le_summary_output=args.ge_le_summary_output,
    )
    print(json.dumps(report.get("summary") or {}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
