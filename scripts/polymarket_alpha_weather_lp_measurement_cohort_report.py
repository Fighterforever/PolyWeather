#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_measurement_cohort import (  # noqa: E402
    build_weather_lp_measurement_cohort_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain paper-only Weather LP measurement cohorts.")
    parser.add_argument("--quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--existing-cohorts", default="evidence/weather_lp_rewards/measurement_cohorts.jsonl")
    parser.add_argument("--cohorts-output", default="evidence/weather_lp_rewards/measurement_cohorts.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/measurement_cohort_report.json")
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_measurement_cohort_report(
        quotes=load_jsonl(args.quotes),
        quote_updates=load_jsonl(args.quote_updates),
        existing_cohorts=load_jsonl(args.existing_cohorts),
        generated_at=args.generated_at,
    )
    write_jsonl(args.cohorts_output, report.get("cohorts") or [])
    compact = {key: value for key, value in report.items() if key not in {"cohorts", "new_cohorts"}}
    compact["artifact_paths"] = {"cohorts": str(args.cohorts_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "active_quote_count": compact.get("active_quote_count"),
                "active_cohort_count": compact.get("active_cohort_count"),
                "cohorts_created_count": compact.get("cohorts_created_count"),
                "next_expected_5m_markout_time": compact.get("next_expected_5m_markout_time"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
