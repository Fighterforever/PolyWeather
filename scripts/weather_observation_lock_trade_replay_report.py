#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_trade_tape_backfill import load_jsonl  # noqa: E402
from src.trading.weather_observation_lock_trade_replay import (  # noqa: E402
    build_observation_lock_trade_replay_report,
    load_json,
    write_json,
    write_jsonl,
)


DEFAULT_TRADABILITY = Path("evidence/historical_replay/observation_lock_tradability_report.json")
DEFAULT_TRADE_TAPE = Path("evidence/historical_markets/polymarket_trade_tape.jsonl")
DEFAULT_CLOSED = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_REPORT = Path("evidence/historical_replay/observation_lock_trade_replay_report.json")
DEFAULT_ROWS = Path("evidence/historical_replay/observation_lock_trade_replay_rows.jsonl")
DEFAULT_ATTRIBUTION = Path("evidence/historical_replay/observation_lock_trade_proxy_attribution.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay observation-lock signals against public trade prints.")
    parser.add_argument("--tradability-report", default=str(DEFAULT_TRADABILITY))
    parser.add_argument("--trade-tape", default=str(DEFAULT_TRADE_TAPE))
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROWS))
    parser.add_argument("--attribution-output", default=str(DEFAULT_ATTRIBUTION))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_observation_lock_trade_replay_report(
        tradability_report=load_json(args.tradability_report),
        trade_tape_rows=load_jsonl(args.trade_tape),
        closed_markets=load_jsonl(args.closed_markets),
        generated_at=args.generated_at,
    )
    write_json(args.summary_output, report)
    write_jsonl(args.rows_output, report["rows"])
    write_json(args.attribution_output, report.get("attribution") or {})
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
