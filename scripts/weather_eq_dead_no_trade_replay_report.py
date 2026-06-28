#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_eq_dead_no_trade_replay import build_eq_dead_no_trade_replay_report, load_jsonl  # noqa: E402


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build eq-dead-NO historical trade replay report.")
    parser.add_argument("--observation-lock-trade-rows", default="evidence/historical_replay/observation_lock_trade_replay_rows.jsonl")
    parser.add_argument("--summary-output", default="evidence/eq_dead_no/eq_dead_no_trade_replay_report.json")
    parser.add_argument("--rows-output", default="evidence/eq_dead_no/eq_dead_no_trade_replay_rows.jsonl")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_eq_dead_no_trade_replay_report(
        observation_lock_trade_rows=load_jsonl(args.observation_lock_trade_rows),
    )
    summary_output = Path(args.summary_output)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    rows_output = Path(args.rows_output)
    rows_output.parent.mkdir(parents=True, exist_ok=True)
    with rows_output.open("w", encoding="utf-8") as handle:
        for row in report["rows"]:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
