#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.market_discovery import (  # noqa: E402
    collect_market_discovery,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "market_discovery_report.json"
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_CLOSED = DEFAULT_ROOT / "closed_markets_snapshot.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Polymarket all-market discovery evidence.")
    parser.add_argument("--active-limit", type=int, default=500)
    parser.add_argument("--closed-limit", type=int, default=300)
    parser.add_argument("--include-order-books", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-orderbook-markets", type=int, default=60)
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--active-output", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--closed-output", default=str(DEFAULT_CLOSED))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = collect_market_discovery(
        active_limit=args.active_limit,
        closed_limit=args.closed_limit,
        include_order_books=bool(args.include_order_books),
        max_orderbook_markets=int(args.max_orderbook_markets),
        generated_at=args.generated_at,
    )
    active_rows = report.pop("active_markets", [])
    closed_rows = report.pop("closed_markets", [])
    write_jsonl(args.active_output, active_rows)
    write_jsonl(args.closed_output, closed_rows)
    report["artifact_paths"] = {
        "active_markets_snapshot": str(args.active_output),
        "closed_markets_snapshot": str(args.closed_output),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "active_market_count": report.get("active_market_count"),
                "closed_market_count": report.get("closed_market_count"),
                "top_categories": [
                    row.get("category") for row in (report.get("top_categories") or [])[:5]
                ],
                "orderbook_available_count": report.get("orderbook_available_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
