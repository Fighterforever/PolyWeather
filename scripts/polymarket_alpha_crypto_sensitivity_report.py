#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.polymarket_alpha_crypto_edge_report import _enrich_active_markets  # noqa: E402
from src.trading.polymarket_alpha.crypto_probability_model import (  # noqa: E402
    build_crypto_touch_sensitivity_report,
    fetch_binance_spot,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_CRYPTO_REPORT = DEFAULT_ROOT / "crypto_probability_edge_report.json"
DEFAULT_REPORT = DEFAULT_ROOT / "crypto_touch_sensitivity_report.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only crypto touch model sensitivity diagnostics.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--crypto-report", default=str(DEFAULT_CRYPTO_REPORT))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--high-since-start-cache-dir", default=str(DEFAULT_ROOT / "binance_klines"))
    parser.add_argument("--metadata-cache-dir", default=str(DEFAULT_ROOT / "gamma_market_metadata"))
    parser.add_argument("--price-history-rows", default=str(DEFAULT_ROOT / "probability_decision_snapshots.jsonl"))
    parser.add_argument("--fetch-gamma-metadata", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--btc-spot", type=float, default=None)
    parser.add_argument("--eth-spot", type=float, default=None)
    parser.add_argument("--fetch-binance-spot", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fetch-orderbooks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fetch-realized-vol", action=argparse.BooleanOptionalAction, default=True)
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
    crypto_report = {}
    crypto_report_path = Path(args.crypto_report)
    if crypto_report_path.exists():
        parsed = json.loads(crypto_report_path.read_text(encoding="utf-8"))
        crypto_report = parsed if isinstance(parsed, dict) else {}
    spot_prices = {}
    if args.btc_spot is not None:
        spot_prices["BTC"] = float(args.btc_spot)
    if args.eth_spot is not None:
        spot_prices["ETH"] = float(args.eth_spot)
    if args.fetch_binance_spot:
        spot_prices.setdefault("BTC", fetch_binance_spot("BTC"))
        spot_prices.setdefault("ETH", fetch_binance_spot("ETH"))
    spot_prices = {key: value for key, value in spot_prices.items() if value is not None}
    report = build_crypto_touch_sensitivity_report(
        active_markets=_enrich_active_markets(args),
        crypto_probability_report=crypto_report,
        spot_prices=spot_prices,
        annual_vols={"BTC": float(args.btc_vol), "ETH": float(args.eth_vol)},
        generated_at=args.generated_at,
        fetch_orderbooks=bool(args.fetch_orderbooks),
        max_orderbook_tokens=int(args.max_orderbook_tokens),
        min_edge=float(args.min_edge),
        cost=float(args.cost),
        min_depth=float(args.min_depth),
        max_spread=float(args.max_spread),
        high_since_start_cache_dir=args.high_since_start_cache_dir,
        fetch_realized_vol=bool(args.fetch_realized_vol),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "verified_not_touched_market_side_count": report.get("verified_not_touched_market_side_count"),
                "sensitivity_fragile_count": report.get("sensitivity_fragile_count"),
                "live_order_path": report.get("live_order_path"),
                "summary_output": args.summary_output,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
