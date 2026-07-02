#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_touch_historical_calibration import (  # noqa: E402
    build_crypto_touch_calibration_report,
    load_jsonl,
    write_json,
    write_jsonl,
)
from src.trading.polymarket_alpha.crypto_market_semantics import classify_crypto_semantics, merge_market_metadata  # noqa: E402
from src.trading.polymarket_alpha.crypto_probability_model import parse_crypto_threshold_market  # noqa: E402
from src.trading.polymarket_readonly import PolymarketReadonlyClient, PolymarketReadonlyError  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate crypto touch-barrier probability model on closed Polymarket markets.")
    parser.add_argument("--closed-markets", default=str(DEFAULT_ROOT / "closed_markets_snapshot.jsonl"))
    parser.add_argument("--decision-snapshots", default=str(DEFAULT_ROOT / "probability_decision_snapshots.jsonl"))
    parser.add_argument("--cache-dir", default=str(DEFAULT_ROOT / "binance_klines"))
    parser.add_argument("--metadata-cache-dir", default=str(DEFAULT_ROOT / "gamma_market_metadata"))
    parser.add_argument("--fetch-gamma-metadata", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_touch_calibration_report.json"))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROOT / "crypto_touch_calibration_rows.jsonl"))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--max-markets", type=int, default=50)
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


def _enrich_closed_markets(args: argparse.Namespace) -> list[dict]:
    rows = load_jsonl(args.closed_markets)
    if not args.fetch_gamma_metadata:
        return rows
    client = PolymarketReadonlyClient()
    enriched = []
    for market in rows:
        if not isinstance(market, dict):
            continue
        if parse_crypto_threshold_market(market) is not None and classify_crypto_semantics(market) == "touch_barrier":
            enriched.append(merge_market_metadata(market, _fetch_gamma_metadata(market, cache_dir=args.metadata_cache_dir, client=client)))
        else:
            enriched.append(market)
    return enriched


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_crypto_touch_calibration_report(
        closed_markets=_enrich_closed_markets(args),
        decision_snapshots=load_jsonl(args.decision_snapshots),
        generated_at=args.generated_at,
        cache_dir=args.cache_dir,
        max_markets=int(args.max_markets),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    compact = {key: value for key, value in report.items() if key != "rows"}
    compact["artifact_paths"] = {"rows": str(args.rows_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "sample_count": report.get("sample_count"),
                "unique_market_count": report.get("unique_market_count"),
                "brier_market": report.get("brier_market"),
                "brier_model": report.get("brier_model"),
                "status": report.get("status"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
