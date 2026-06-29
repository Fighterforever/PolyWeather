#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_touch_forward_validation import (  # noqa: E402
    build_crypto_touch_forward_validation_report,
    write_json,
)
from src.trading.polymarket_alpha.probability_edge_journal import load_jsonl  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build crypto touch paper-only forward validation report.")
    parser.add_argument("--formal-fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--formal-markouts", default=str(DEFAULT_PAPER / "markouts.jsonl"))
    parser.add_argument("--near-miss-watch", default=str(DEFAULT_ROOT / "crypto_touch_near_miss_watch.jsonl"))
    parser.add_argument("--near-miss-markouts", default=str(DEFAULT_ROOT / "crypto_touch_near_miss_markouts.jsonl"))
    parser.add_argument("--invalidated-old-fills", default=str(DEFAULT_PAPER / "invalidated_fills.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_touch_forward_validation_report.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_crypto_touch_forward_validation_report(
        formal_fills=load_jsonl(args.formal_fills),
        formal_markouts=load_jsonl(args.formal_markouts),
        near_miss_watch=load_jsonl(args.near_miss_watch),
        near_miss_markouts=load_jsonl(args.near_miss_markouts),
        invalidated_old_fills=load_jsonl(args.invalidated_old_fills),
    )
    report["artifact_paths"] = {
        "formal_fills": str(args.formal_fills),
        "formal_markouts": str(args.formal_markouts),
        "near_miss_watch": str(args.near_miss_watch),
        "near_miss_markouts": str(args.near_miss_markouts),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "formal_fill_count": report.get("formal_fills", {}).get("fill_count"),
                "formal_available_markout_count": report.get("formal_fills", {}).get("available_markout_count"),
                "near_miss_available_markout_count": report.get("near_miss_watch", {}).get("available_markout_count"),
                "verdict": report.get("verdict", {}).get("status"),
                "do_not_lower_threshold": report.get("verdict", {}).get("do_not_lower_threshold"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
