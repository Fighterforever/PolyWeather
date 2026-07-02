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

from src.trading.weather_eq_dead_no_markout import build_eq_dead_no_markout_report, load_jsonl  # noqa: E402


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build eq-dead-NO paper markout report.")
    parser.add_argument("--paper-fill-path", default="evidence/eq_dead_no/paper_fills.jsonl")
    parser.add_argument("--orderbook-snapshot-path", default="evidence/eq_dead_no/orderbook_snapshots.jsonl")
    parser.add_argument("--markout-output", default="evidence/eq_dead_no/markouts.jsonl")
    parser.add_argument("--summary-output", default="evidence/eq_dead_no/markout_report.json")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_eq_dead_no_markout_report(
        fills=load_jsonl(args.paper_fill_path),
        orderbook_snapshots=load_jsonl(args.orderbook_snapshot_path),
    )
    markout_output = Path(args.markout_output)
    markout_output.parent.mkdir(parents=True, exist_ok=True)
    with markout_output.open("w", encoding="utf-8") as handle:
        for row in report["rows"]:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {key: value for key, value in report.items() if key != "rows"}
    summary["paths"] = {"markouts": str(markout_output)}
    output = Path(args.summary_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
