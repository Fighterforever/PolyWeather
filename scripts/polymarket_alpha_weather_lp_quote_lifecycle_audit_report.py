#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_paper_journal import (  # noqa: E402
    build_weather_lp_quote_lifecycle_audit,
    load_jsonl,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Weather LP paper quote lifecycle stability.")
    parser.add_argument("--quotes", default="evidence/weather_lp_rewards/paper_quotes.jsonl")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/quote_lifecycle_audit_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_quote_lifecycle_audit(
        quotes=load_jsonl(args.quotes),
        quote_updates=load_jsonl(args.quote_updates),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "unique_quote_id_count": report.get("unique_quote_id_count"),
                "quote_update_count": report.get("quote_update_count"),
                "updates_per_quote_median": report.get("updates_per_quote_median"),
                "conclusion": report.get("conclusion"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
