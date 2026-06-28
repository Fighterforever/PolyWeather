#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_edge_journal import (  # noqa: E402
    build_markout_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit forward markouts for paper-only probability edge fills.")
    parser.add_argument("--fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--price-rows", default=str(DEFAULT_ROOT / "probability_decision_snapshots.jsonl"))
    parser.add_argument("--orderbook-snapshots", default=str(DEFAULT_PAPER / "orderbook_snapshots.jsonl"))
    parser.add_argument("--markouts-output", default=str(DEFAULT_PAPER / "markouts.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_PAPER / "markout_report.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_markout_report(
        fills=load_jsonl(args.fills),
        price_rows=load_jsonl(args.price_rows),
        orderbook_snapshots=load_jsonl(args.orderbook_snapshots),
    )
    write_jsonl(args.markouts_output, report.get("markouts") or [])
    report["artifact_paths"] = {"markouts": str(args.markouts_output)}
    write_json(args.summary_output, {key: value for key, value in report.items() if key != "markouts"})
    print(
        json.dumps(
            {
                "fill_count": report.get("fill_count"),
                "markout_count": report.get("markout_count"),
                "available_markout_count": report.get("available_markout_count"),
                "mean_markout": report.get("mean_markout"),
                "mean_markout_cents": report.get("mean_markout_cents"),
                "markout_status": report.get("markout_status"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
