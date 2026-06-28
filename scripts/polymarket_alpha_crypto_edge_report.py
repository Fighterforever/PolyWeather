#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_probability_model import (  # noqa: E402
    build_crypto_probability_edge_report,
    fetch_binance_spot,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "crypto_probability_edge_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "crypto_probability_candidates.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only crypto threshold probability edge scan.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--btc-spot", type=float, default=None)
    parser.add_argument("--eth-spot", type=float, default=None)
    parser.add_argument("--fetch-binance-spot", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fetch-orderbooks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-orderbook-tokens", type=int, default=200)
    parser.add_argument("--btc-vol", type=float, default=0.55)
    parser.add_argument("--eth-vol", type=float, default=0.70)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--cost", type=float, default=0.01)
    parser.add_argument("--min-depth", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.15)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    spot_prices = {}
    if args.btc_spot is not None:
        spot_prices["BTC"] = float(args.btc_spot)
    if args.eth_spot is not None:
        spot_prices["ETH"] = float(args.eth_spot)
    if args.fetch_binance_spot:
        spot_prices.setdefault("BTC", fetch_binance_spot("BTC"))
        spot_prices.setdefault("ETH", fetch_binance_spot("ETH"))
    spot_prices = {key: value for key, value in spot_prices.items() if value is not None}
    report = build_crypto_probability_edge_report(
        active_markets=load_jsonl(args.active_markets),
        spot_prices=spot_prices,
        annual_vols={"BTC": float(args.btc_vol), "ETH": float(args.eth_vol)},
        generated_at=args.generated_at,
        fetch_orderbooks=bool(args.fetch_orderbooks),
        max_orderbook_tokens=int(args.max_orderbook_tokens),
        min_edge=float(args.min_edge),
        cost=float(args.cost),
        min_depth=float(args.min_depth),
        max_spread=float(args.max_spread),
    )
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    compact = {key: value for key, value in report.items() if key not in {"candidates", "watch_rows"}}
    compact["artifact_paths"] = {"crypto_probability_candidates": str(args.candidates_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "parsed_crypto_market_count": report.get("parsed_crypto_market_count"),
                "model_ready_count": report.get("model_ready_count"),
                "executable_price_available_count": report.get("executable_price_available_count"),
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
