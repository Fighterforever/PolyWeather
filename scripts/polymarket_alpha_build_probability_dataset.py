#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_dataset import (  # noqa: E402
    build_probability_dataset,
    fetch_price_history_for_closed_markets,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_CLOSED = DEFAULT_ROOT / "closed_markets_snapshot.jsonl"
DEFAULT_PRICE = Path("evidence/historical_markets/polymarket_price_history.jsonl")
DEFAULT_TRADE = Path("evidence/historical_markets/polymarket_trade_tape.jsonl")
DEFAULT_FETCHED_PRICE = DEFAULT_ROOT / "probability_price_history.jsonl"
DEFAULT_DATASET = DEFAULT_ROOT / "probability_dataset.jsonl"
DEFAULT_MANIFEST = DEFAULT_ROOT / "probability_dataset_manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Polymarket-only historical probability dataset.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED))
    parser.add_argument("--price-history", action="append", default=[str(DEFAULT_PRICE)])
    parser.add_argument("--trade-tape", action="append", default=[str(DEFAULT_TRADE)])
    parser.add_argument("--fetch-missing-price-history", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--max-fetch-tokens", type=int, default=0)
    parser.add_argument("--fetched-price-output", default=str(DEFAULT_FETCHED_PRICE))
    parser.add_argument("--dataset-output", default=str(DEFAULT_DATASET))
    parser.add_argument("--manifest-output", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    active = load_jsonl(args.active_markets)
    closed = load_jsonl(args.closed_markets)
    price_rows = []
    for path in args.price_history or []:
        price_rows.extend(load_jsonl(path))
    fetched_rows = []
    if args.fetch_missing_price_history and int(args.max_fetch_tokens) > 0:
        fetched_rows = fetch_price_history_for_closed_markets(active + closed, max_tokens=int(args.max_fetch_tokens))
        write_jsonl(args.fetched_price_output, fetched_rows)
    price_rows.extend(fetched_rows)
    trade_rows = []
    for path in args.trade_tape or []:
        trade_rows.extend(load_jsonl(path))
    report = build_probability_dataset(
        active_markets=active,
        closed_markets=closed,
        price_history_rows=price_rows,
        trade_rows=trade_rows,
        generated_at=args.generated_at,
    )
    write_jsonl(args.dataset_output, report["rows"])
    manifest = dict(report["manifest"])
    manifest["artifact_paths"] = {
        "probability_dataset": str(args.dataset_output),
        "fetched_price_history": str(args.fetched_price_output),
    }
    write_json(args.manifest_output, manifest)
    print(
        json.dumps(
            {
                "row_count": manifest.get("row_count"),
                "resolved_row_count": manifest.get("resolved_row_count"),
                "category_count": len(manifest.get("category_counts") or []),
                "no_lookahead_violation_count": manifest.get("no_lookahead_violation_count"),
                "live_order_path": manifest.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
