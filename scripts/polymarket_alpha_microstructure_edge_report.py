#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.microstructure_edge_scanner import (  # noqa: E402
    load_jsonl,
    scan_microstructure_edges,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_edge_report.json"
DEFAULT_WATCH = DEFAULT_ROOT / "microstructure_watch_rows.jsonl"
DEFAULT_FILLS = DEFAULT_ROOT / "microstructure_paper_fills.jsonl"
DEFAULT_SNAPSHOTS = DEFAULT_ROOT / "microstructure_orderbook_snapshots.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only all-market microstructure edge scan.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--watch-output", default=str(DEFAULT_WATCH))
    parser.add_argument("--paper-fills-output", default=str(DEFAULT_FILLS))
    parser.add_argument("--orderbook-snapshots-output", default=str(DEFAULT_SNAPSHOTS))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--allowed-category", action="append", default=[])
    parser.add_argument("--paper-mode", choices=("watch_only", "paper_fill"), default="watch_only")
    parser.add_argument("--min-depth", type=float, default=25.0)
    parser.add_argument("--min-spread", type=float, default=0.02)
    parser.add_argument("--max-spread", type=float, default=0.18)
    parser.add_argument("--min-price", type=float, default=0.02)
    parser.add_argument("--max-price", type=float, default=0.98)
    parser.add_argument("--imbalance-threshold", type=float, default=0.55)
    return parser.parse_args(argv)


def _append_jsonl(path: str | Path, rows: list[dict], key: str) -> int:
    existing = load_jsonl(path)
    merged = {str(row.get(key)): row for row in existing if row.get(key)}
    for row in rows:
        if isinstance(row, dict) and row.get(key):
            merged[str(row[key])] = row
    return write_jsonl(path, sorted(merged.values(), key=lambda row: str(row.get("entry_time") or row.get("recorded_at") or "")))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = scan_microstructure_edges(
        active_markets=load_jsonl(args.active_markets),
        generated_at=args.generated_at,
        allowed_categories=args.allowed_category,
        paper_mode=args.paper_mode,
        min_depth=float(args.min_depth),
        min_spread=float(args.min_spread),
        max_spread=float(args.max_spread),
        min_price=float(args.min_price),
        max_price=float(args.max_price),
        imbalance_threshold=float(args.imbalance_threshold),
    )
    _append_jsonl(args.watch_output, report.get("watch_rows") or [], "watch_id")
    _append_jsonl(args.paper_fills_output, report.get("paper_fills") or [], "fill_id")
    _append_jsonl(args.orderbook_snapshots_output, report.get("orderbook_snapshots") or [], "orderbook_snapshot_id")
    compact = {key: value for key, value in report.items() if key not in {"watch_rows", "paper_fills", "orderbook_snapshots"}}
    compact["artifact_paths"] = {
        "microstructure_watch_rows": str(args.watch_output),
        "microstructure_paper_fills": str(args.paper_fills_output),
        "microstructure_orderbook_snapshots": str(args.orderbook_snapshots_output),
    }
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "scanned_market_count": report.get("scanned_market_count"),
                "candidate_count": report.get("candidate_count"),
                "paper_fill_count": report.get("paper_fill_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
