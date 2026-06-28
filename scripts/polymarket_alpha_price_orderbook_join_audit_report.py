#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.price_orderbook_join_audit import (  # noqa: E402
    build_price_orderbook_join_audit_report,
    load_jsonl,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_SNAPSHOTS = DEFAULT_ROOT / "probability_decision_snapshots.jsonl"
DEFAULT_PRICE = DEFAULT_ROOT / "probability_price_history.jsonl"
DEFAULT_OUTPUT = DEFAULT_ROOT / "price_orderbook_join_audit_report.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Polymarket historical price and active orderbook joins.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOTS))
    parser.add_argument("--price-history", default=str(DEFAULT_PRICE))
    parser.add_argument("--fetch-orderbooks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-orderbook-tokens", type=int, default=200)
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_price_orderbook_join_audit_report(
        active_markets=load_jsonl(args.active_markets),
        snapshot_rows=load_jsonl(args.snapshots),
        price_history_rows=load_jsonl(args.price_history),
        fetch_orderbooks=bool(args.fetch_orderbooks),
        max_orderbook_tokens=int(args.max_orderbook_tokens),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "active_token_count": report.get("active_orderbook_join", {}).get("active_token_count"),
                "orderbook_fetch_success_count": report.get("active_orderbook_join", {}).get("orderbook_fetch_success_count"),
                "crypto_yes_best_ask_available_count": report.get("crypto_specific_diagnostics", {}).get("crypto_yes_best_ask_available_count"),
                "crypto_no_best_ask_available_count": report.get("crypto_specific_diagnostics", {}).get("crypto_no_best_ask_available_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
