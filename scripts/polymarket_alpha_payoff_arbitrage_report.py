#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.payoff_matrix_arbitrage import (  # noqa: E402
    build_payoff_arbitrage_report,
    load_json,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_CATALOG = DEFAULT_ROOT / "market_family_catalog.json"
DEFAULT_FAMILY_ROWS = DEFAULT_ROOT / "market_family_rows.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "payoff_arbitrage_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "payoff_arbitrage_candidates.jsonl"
DEFAULT_NEAR_MISSES = DEFAULT_ROOT / "payoff_arbitrage_near_misses.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only Polymarket payoff-matrix arbitrage scan.")
    parser.add_argument("--family-catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--family-rows", default=str(DEFAULT_FAMILY_ROWS))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--near-misses-output", default=str(DEFAULT_NEAR_MISSES))
    parser.add_argument("--min-edge-cents", type=float, default=0.25)
    parser.add_argument("--min-depth", type=float, default=1.0)
    parser.add_argument("--cost-cents", type=float, default=0.0)
    parser.add_argument("--allow-bruteforce-without-scipy", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(argv)


def _load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    catalog = load_json(args.family_catalog)
    if not catalog.get("families"):
        catalog["families"] = _load_jsonl(args.family_rows)
    report = build_payoff_arbitrage_report(
        catalog,
        min_edge_cents=float(args.min_edge_cents),
        min_depth=float(args.min_depth),
        cost_cents=float(args.cost_cents),
        allow_bruteforce_without_scipy=bool(args.allow_bruteforce_without_scipy),
    )
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    write_jsonl(args.near_misses_output, report.get("near_misses") or [])
    report["artifact_paths"] = {
        "payoff_arbitrage_candidates": str(args.candidates_output),
        "payoff_arbitrage_near_misses": str(args.near_misses_output),
    }
    compact_report = {key: value for key, value in report.items() if key != "family_rows"}
    compact_report["family_row_count"] = len(report.get("family_rows") or [])
    write_json(args.summary_output, compact_report)
    print(
        json.dumps(
            {
                "family_count": report.get("family_count"),
                "structural_candidate_count": report.get("structural_candidate_count"),
                "structural_near_miss_count": report.get("structural_near_miss_count"),
                "best_edge_cents": report.get("best_edge_cents"),
                "scipy_available": report.get("scipy_available"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
