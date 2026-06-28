#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.rule_confusion_scanner import (  # noqa: E402
    load_jsonl,
    scan_rule_confusion,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "rule_confusion_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "rule_confusion_candidates.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan Polymarket active markets for paper-only rule confusion candidates.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--min-liquidity", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = scan_rule_confusion(load_jsonl(args.active_markets), min_liquidity=float(args.min_liquidity))
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    report["artifact_paths"] = {"candidates": str(args.candidates_output)}
    write_json(args.summary_output, {key: value for key, value in report.items() if key != "candidates"})
    print(
        json.dumps(
            {
                "candidate_count": report.get("candidate_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
