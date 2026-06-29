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
    build_microstructure_policy_sweep,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_policy_sweep_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "microstructure_policy_candidates.jsonl"
DEFAULT_TAKER_FILLS = DEFAULT_ROOT / "microstructure_paper_fills.jsonl"
DEFAULT_MAKER_QUOTES = DEFAULT_ROOT / "microstructure_maker_quotes.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expand microstructure watch rows into paper-only action policies.")
    parser.add_argument("--watch-rows", default=str(DEFAULT_ROOT / "microstructure_watch_rows.jsonl"))
    parser.add_argument("--orderbook-snapshots", default=str(DEFAULT_ROOT / "microstructure_orderbook_snapshots.jsonl"))
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--taker-fills-output", default=str(DEFAULT_TAKER_FILLS))
    parser.add_argument("--maker-quotes-output", default=str(DEFAULT_MAKER_QUOTES))
    parser.add_argument("--imbalance-threshold", type=float, default=0.55)
    parser.add_argument("--momentum-threshold", type=float, default=0.05)
    parser.add_argument("--min-spread", type=float, default=0.02)
    parser.add_argument("--max-spread", type=float, default=0.18)
    parser.add_argument("--min-depth", type=float, default=25.0)
    parser.add_argument("--maker-margin", type=float, default=0.01)
    return parser.parse_args(argv)


def _merge_jsonl(path: str | Path, rows: list[dict], key: str) -> int:
    existing = [row for row in load_jsonl(path) if row.get(key)]
    merged = {str(row[key]): row for row in existing}
    for row in rows:
        if isinstance(row, dict) and row.get(key):
            merged[str(row[key])] = row
    return write_jsonl(path, sorted(merged.values(), key=lambda row: str(row.get("entry_time") or row.get("recorded_at") or "")))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_microstructure_policy_sweep(
        watch_rows=load_jsonl(args.watch_rows),
        orderbook_snapshots=load_jsonl(args.orderbook_snapshots),
        active_markets=load_jsonl(args.active_markets),
        imbalance_threshold=float(args.imbalance_threshold),
        momentum_threshold=float(args.momentum_threshold),
        min_spread=float(args.min_spread),
        max_spread=float(args.max_spread),
        min_depth=float(args.min_depth),
        maker_margin=float(args.maker_margin),
    )
    _merge_jsonl(args.candidates_output, report.get("candidates") or [], "candidate_id")
    _merge_jsonl(args.taker_fills_output, report.get("taker_fills") or [], "fill_id")
    _merge_jsonl(args.maker_quotes_output, report.get("maker_quotes") or [], "quote_id")
    compact = {key: value for key, value in report.items() if key not in {"candidates", "taker_fills", "maker_quotes"}}
    compact["artifact_paths"] = {
        "policy_candidates": str(args.candidates_output),
        "taker_fills": str(args.taker_fills_output),
        "maker_quotes": str(args.maker_quotes_output),
    }
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "input_watch_count": report.get("input_watch_count"),
                "policy_candidate_count": report.get("policy_candidate_count"),
                "taker_fill_count": report.get("taker_fill_count"),
                "maker_quote_count": report.get("maker_quote_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
