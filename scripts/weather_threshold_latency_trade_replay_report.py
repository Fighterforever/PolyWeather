#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_threshold_latency_trade_replay import (  # noqa: E402
    build_threshold_latency_trade_replay_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_CLOSED = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_OBSERVATIONS = Path("evidence/official_observations/metar_intraday_history.jsonl")
DEFAULT_TRADE_TAPE = Path("evidence/historical_markets/polymarket_trade_tape.jsonl")
DEFAULT_REPORT = Path("evidence/historical_replay/threshold_latency_trade_replay_report.json")
DEFAULT_ROWS = Path("evidence/historical_replay/threshold_latency_trade_replay_rows.jsonl")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay historical threshold-latency events against public trade prints.")
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED))
    parser.add_argument("--intraday-observations", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--trade-tape", default=str(DEFAULT_TRADE_TAPE))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROWS))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--near-margin-c", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_threshold_latency_trade_replay_report(
        closed_markets=load_jsonl(args.closed_markets),
        observations=load_jsonl(args.intraday_observations),
        trade_tape_rows=load_jsonl(args.trade_tape),
        generated_at=args.generated_at,
        near_margin_c=args.near_margin_c,
    )
    write_json(args.summary_output, report)
    write_jsonl(args.rows_output, report["rows"])
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
