#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.active_probability_edge_scanner import (  # noqa: E402
    load_json,
    load_jsonl,
    scan_active_probability_edges,
    write_json,
    write_jsonl,
)
from src.trading.polymarket_alpha.probability_edge_journal import build_probability_edge_fills_with_snapshots  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_MODEL = DEFAULT_ROOT / "probability_edge_model_report.json"
DEFAULT_FOCUS = DEFAULT_ROOT / "category_focus_report.json"
DEFAULT_CRYPTO = DEFAULT_ROOT / "crypto_probability_edge_report.json"
DEFAULT_CRYPTO_CANDIDATES = DEFAULT_ROOT / "crypto_probability_candidates.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "active_probability_edge_report.json"
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan active Polymarket markets with the paper-only probability edge model.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--model-report", default=str(DEFAULT_MODEL))
    parser.add_argument("--focus-report", default=str(DEFAULT_FOCUS))
    parser.add_argument("--crypto-report", default=str(DEFAULT_CRYPTO))
    parser.add_argument("--crypto-candidates", default=str(DEFAULT_CRYPTO_CANDIDATES))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--fills-output", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--watch-rows-output", default=str(DEFAULT_PAPER / "watch_rows.jsonl"))
    parser.add_argument("--orderbook-snapshots-output", default=str(DEFAULT_PAPER / "orderbook_snapshots.jsonl"))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--min-depth", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.15)
    parser.add_argument("--cost", type=float, default=0.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    crypto_report = load_json(args.crypto_report)
    crypto_candidates = load_jsonl(args.crypto_candidates)
    if crypto_candidates:
        crypto_report["candidates"] = crypto_candidates
    active_markets = load_jsonl(args.active_markets)
    report = scan_active_probability_edges(
        active_markets=active_markets,
        model_report=load_json(args.model_report),
        focus_report=load_json(args.focus_report),
        crypto_report=crypto_report,
        min_edge=float(args.min_edge),
        min_depth=float(args.min_depth),
        max_spread=float(args.max_spread),
        cost=float(args.cost),
    )
    journal = build_probability_edge_fills_with_snapshots(
        candidates=report.get("candidates") or [],
        active_markets=active_markets,
        recorded_at=args.generated_at,
    )
    write_jsonl(args.orderbook_snapshots_output, journal.get("orderbook_snapshots") or [])
    write_jsonl(args.fills_output, journal.get("fills") or [])
    write_jsonl(args.watch_rows_output, report.get("watch_rows") or [])
    report["paper_fill_count"] = len(journal.get("fills") or [])
    report["candidate_count"] = len(journal.get("fills") or [])
    report["orderbook_snapshot_id_null_count"] = journal.get("orderbook_snapshot_id_null_count")
    report["artifact_paths"] = {
        "fills": str(args.fills_output),
        "watch_rows": str(args.watch_rows_output),
        "orderbook_snapshots": str(args.orderbook_snapshots_output),
    }
    write_json(args.summary_output, {key: value for key, value in report.items() if key not in {"candidates", "watch_rows"}})
    print(
        json.dumps(
            {
                "candidate_count": report.get("candidate_count"),
                "scanned_active_market_count": report.get("scanned_active_market_count"),
                "model_ready_count": report.get("model_ready_count"),
                "paper_fill_count": report.get("paper_fill_count"),
                "focus_categories": report.get("focus_categories"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
