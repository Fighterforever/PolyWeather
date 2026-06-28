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
from src.trading.polymarket_alpha.crypto_market_semantics import merge_market_metadata  # noqa: E402
from src.trading.polymarket_readonly import PolymarketReadonlyClient, PolymarketReadonlyError  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "crypto_probability_edge_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "crypto_probability_candidates.jsonl"
DEFAULT_SEMANTICS_AUDIT = DEFAULT_ROOT / "crypto_semantics_audit_report.json"
DEFAULT_NEAR_MISS_WATCH = DEFAULT_ROOT / "crypto_touch_near_miss_watch.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only crypto threshold probability edge scan.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--near-miss-watch-output", default=str(DEFAULT_NEAR_MISS_WATCH))
    parser.add_argument("--semantics-audit", default=str(DEFAULT_SEMANTICS_AUDIT))
    parser.add_argument("--high-since-start-cache-dir", default=str(DEFAULT_ROOT / "binance_klines"))
    parser.add_argument("--metadata-cache-dir", default=str(DEFAULT_ROOT / "gamma_market_metadata"))
    parser.add_argument("--price-history-rows", default=str(DEFAULT_ROOT / "probability_decision_snapshots.jsonl"))
    parser.add_argument("--fetch-gamma-metadata", action=argparse.BooleanOptionalAction, default=True)
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


def _metadata_cache_path(cache_dir: str | Path, market: dict) -> Path:
    identifier = str(market.get("market_id") or market.get("market_slug") or "missing")
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in identifier)
    return Path(cache_dir) / f"{safe}.json"


def _fetch_gamma_metadata(market: dict, *, cache_dir: str | Path, client: PolymarketReadonlyClient) -> dict:
    cache_path = _metadata_cache_path(cache_dir, market)
    if cache_path.exists():
        try:
            parsed = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            return parsed
    metadata = {}
    try:
        if market.get("market_id"):
            metadata = client.get_market_by_id(str(market.get("market_id")))
        if not metadata and market.get("market_slug"):
            metadata = client.find_market_by_slug(str(market.get("market_slug"))) or {}
    except PolymarketReadonlyError:
        return {}
    if metadata:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return metadata if isinstance(metadata, dict) else {}


def _earliest_price_history_by_slug(path: str | Path) -> dict[str, str]:
    earliest: dict[str, str] = {}
    for row in load_jsonl(path):
        slug = str(row.get("market_slug") or "")
        timestamp = str(row.get("decision_time") or row.get("timestamp") or "")
        if slug and timestamp and (slug not in earliest or timestamp < earliest[slug]):
            earliest[slug] = timestamp
    return earliest


def _enrich_active_markets(args: argparse.Namespace) -> list[dict]:
    rows = load_jsonl(args.active_markets)
    earliest_by_slug = _earliest_price_history_by_slug(args.price_history_rows)
    client = PolymarketReadonlyClient()
    enriched_rows = []
    for row in rows:
        market = dict(row)
        if args.fetch_gamma_metadata and market.get("active") and str(market.get("category") or "") in {"crypto", "bitcoin", "ethereum", "crypto_prices"}:
            market = merge_market_metadata(market, _fetch_gamma_metadata(market, cache_dir=args.metadata_cache_dir, client=client))
        slug = str(market.get("market_slug") or "")
        if not market.get("earliest_price_history_timestamp") and earliest_by_slug.get(slug):
            market["earliest_price_history_timestamp"] = earliest_by_slug[slug]
        enriched_rows.append(market)
    return enriched_rows


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
        active_markets=_enrich_active_markets(args),
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
    )
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    write_jsonl(args.near_miss_watch_output, report.get("near_miss_watch") or [])
    compact = {key: value for key, value in report.items() if key not in {"candidates", "watch_rows", "near_miss_watch"}}
    semantics_audit = {}
    audit_path = Path(args.semantics_audit)
    if audit_path.exists():
        try:
            parsed = json.loads(audit_path.read_text(encoding="utf-8"))
            semantics_audit = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            semantics_audit = {}
    compact["old_candidate_count"] = semantics_audit.get("fill_count")
    compact["repriced_candidate_count"] = report.get("candidate_count")
    compact["pre_reprice_invalidated_due_semantics_count"] = semantics_audit.get("invalidated_due_semantics_count")
    compact["artifact_paths"] = {
        "crypto_probability_candidates": str(args.candidates_output),
        "crypto_touch_near_miss_watch": str(args.near_miss_watch_output),
    }
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
