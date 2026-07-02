#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.microstructure_policy_sweep import (  # noqa: E402
    build_microstructure_policy_markout_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_WATCH = DEFAULT_ROOT / "microstructure_watch_rows.jsonl"
DEFAULT_FILLS = DEFAULT_ROOT / "microstructure_paper_fills.jsonl"
DEFAULT_MAKER_QUOTES = DEFAULT_ROOT / "microstructure_maker_quotes.jsonl"
DEFAULT_SNAPSHOTS = DEFAULT_ROOT / "microstructure_orderbook_snapshots.jsonl"
DEFAULT_FOLLOWUP = DEFAULT_ROOT / "microstructure_followup_orderbook_snapshots.jsonl"
DEFAULT_MARKOUTS = DEFAULT_ROOT / "microstructure_markouts.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_markout_report.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit short-horizon markouts for paper-only microstructure watch rows.")
    parser.add_argument("--watch-rows", default=str(DEFAULT_WATCH))
    parser.add_argument("--paper-fills", default=str(DEFAULT_FILLS))
    parser.add_argument("--maker-quotes", default=str(DEFAULT_MAKER_QUOTES))
    parser.add_argument("--orderbook-snapshots", default=str(DEFAULT_SNAPSHOTS))
    parser.add_argument("--followup-orderbook-snapshots", default=str(DEFAULT_FOLLOWUP))
    parser.add_argument("--markouts-output", default=str(DEFAULT_MARKOUTS))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_microstructure_policy_markout_report(
        taker_fills=load_jsonl(args.paper_fills),
        maker_quotes=load_jsonl(args.maker_quotes),
        orderbook_snapshots=load_jsonl(args.orderbook_snapshots) + load_jsonl(args.followup_orderbook_snapshots),
    )
    write_jsonl(args.markouts_output, report.get("markouts") or [])
    compact = {key: value for key, value in report.items() if key != "markouts"}
    compact["artifact_paths"] = {"microstructure_markouts": str(args.markouts_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "taker_fill_count": report.get("taker_fill_count"),
                "maker_quote_count": report.get("maker_quote_count"),
                "maker_inferred_fill_count": report.get("maker_inferred_fill_count"),
                "available_markout_count": report.get("available_markout_count"),
                "mean_markout_cents": report.get("mean_markout_cents"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
