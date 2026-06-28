#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_market_semantics import classify_crypto_parse_gap, classify_crypto_semantics, write_json  # noqa: E402
from src.trading.polymarket_alpha.crypto_probability_model import CRYPTO_CATEGORIES, load_jsonl, parse_crypto_threshold_market  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit crypto threshold parser coverage for active Polymarket rows.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_parse_coverage_report.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    rows = []
    reason_counts: Counter[str] = Counter()
    parsed_count = 0
    scanned_count = 0
    for market in load_jsonl(args.active_markets):
        if not isinstance(market, dict) or not market.get("active"):
            continue
        if str(market.get("category") or "") not in CRYPTO_CATEGORIES:
            continue
        scanned_count += 1
        parsed = parse_crypto_threshold_market(market)
        if parsed:
            parsed_count += 1
            rows.append(
                {
                    "market_slug": market.get("market_slug"),
                    "parsed": True,
                    "reason": None,
                    "asset": parsed.get("asset"),
                    "threshold": parsed.get("threshold"),
                    "semantics_type": classify_crypto_semantics(market),
                    "paper_only": True,
                    "live_order_path": False,
                }
            )
            continue
        reason = classify_crypto_parse_gap(market)
        reason_counts[reason] += 1
        rows.append(
            {
                "market_slug": market.get("market_slug"),
                "parsed": False,
                "reason": reason,
                "title": market.get("title") or market.get("question"),
                "category": market.get("category"),
                "paper_only": True,
                "live_order_path": False,
            }
        )
    report = {
        "schema_version": "polyweather_polymarket_alpha_crypto_parse_coverage_report.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "scanned_crypto_market_count": scanned_count,
        "parsed_crypto_market_count": parsed_count,
        "unparseable_crypto_market_count": scanned_count - parsed_count,
        "unparseable_reason_counts": [{"reason": key, "count": reason_counts[key]} for key in sorted(reason_counts)],
        "rows": rows,
    }
    write_json(args.summary_output, report)
    print(json.dumps({key: report.get(key) for key in (
        "scanned_crypto_market_count",
        "parsed_crypto_market_count",
        "unparseable_crypto_market_count",
        "unparseable_reason_counts",
        "live_order_path",
    )}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
