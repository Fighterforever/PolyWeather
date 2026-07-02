#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_trade_tape_backfill import (  # noqa: E402
    build_trade_tape_backfill_report,
    load_jsonl,
    load_locked_signals_report,
    write_json,
    write_jsonl,
)


DEFAULT_TRADABILITY = Path("evidence/historical_replay/observation_lock_tradability_report.json")
DEFAULT_CLOSED = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_TRADE_TAPE = Path("evidence/historical_markets/polymarket_trade_tape.jsonl")
DEFAULT_MANIFEST = Path("evidence/historical_markets/polymarket_trade_tape_manifest.json")
DEFAULT_GAP_REPORT = Path("evidence/historical_markets/polymarket_trade_tape_gap_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill public Polymarket trade tape for paper-only weather replay.")
    parser.add_argument("--tradability-report", default=str(DEFAULT_TRADABILITY))
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED))
    parser.add_argument("--trade-tape-output", default=str(DEFAULT_TRADE_TAPE))
    parser.add_argument("--manifest-output", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--gap-report-output", default=str(DEFAULT_GAP_REPORT))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_trade_tape_backfill_report(
        locked_signals=load_locked_signals_report(args.tradability_report),
        closed_markets=load_jsonl(args.closed_markets),
        generated_at=args.generated_at,
    )
    write_jsonl(args.trade_tape_output, report["trades"])
    write_json(args.manifest_output, report["manifest"])
    write_json(args.gap_report_output, report["gap_report"])
    print(json.dumps(report["manifest"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
