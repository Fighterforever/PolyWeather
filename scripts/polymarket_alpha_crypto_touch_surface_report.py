#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_touch_surface import (  # noqa: E402
    build_crypto_touch_surface_report,
    load_json,
    write_json,
    write_jsonl,
)
from src.trading.polymarket_alpha.probability_edge_journal import load_jsonl  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build crypto touch barrier surface relative-value report.")
    parser.add_argument("--crypto-report", default=str(DEFAULT_ROOT / "crypto_probability_edge_report.json"))
    parser.add_argument("--crypto-candidates", default=str(DEFAULT_ROOT / "crypto_probability_candidates.jsonl"))
    parser.add_argument("--formal-fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--near-miss-watch", default=str(DEFAULT_ROOT / "crypto_touch_near_miss_watch.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_touch_surface_report.json"))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROOT / "crypto_touch_surface_rows.jsonl"))
    parser.add_argument("--min-edge", type=float, default=0.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    crypto_report = load_json(args.crypto_report)
    candidates = load_jsonl(args.crypto_candidates)
    if candidates:
        crypto_report["candidates"] = candidates
    near_miss_watch = load_jsonl(args.near_miss_watch)
    if near_miss_watch:
        crypto_report["near_miss_watch"] = near_miss_watch
    report = build_crypto_touch_surface_report(
        crypto_probability_report=crypto_report,
        formal_fills=load_jsonl(args.formal_fills),
        near_miss_watch=near_miss_watch,
        min_edge=float(args.min_edge),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    report["artifact_paths"] = {
        "crypto_report": str(args.crypto_report),
        "crypto_candidates": str(args.crypto_candidates),
        "formal_fills": str(args.formal_fills),
        "near_miss_watch": str(args.near_miss_watch),
        "rows": str(args.rows_output),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "group_count": report.get("group_count"),
                "market_count": report.get("market_count"),
                "monotonic_violation_count": report.get("monotonic_violation_count"),
                "relative_value_candidate_count": report.get("relative_value_candidate_count"),
                "surface_near_miss_count": report.get("surface_near_miss_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
