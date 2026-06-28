#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.binance_crypto_history import (  # noqa: E402
    summarize_high_since_start_rows,
    verify_high_since_start,
    write_json,
)
from src.trading.polymarket_alpha.crypto_market_semantics import (  # noqa: E402
    classify_crypto_semantics,
    merge_market_metadata,
    resolve_market_creation_time,
)
from src.trading.polymarket_alpha.crypto_probability_model import load_jsonl, parse_crypto_threshold_market  # noqa: E402
from src.trading.polymarket_readonly import PolymarketReadonlyClient, PolymarketReadonlyError  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify crypto touch-barrier markets using Binance 1m high prices.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_high_since_start_report.json"))
    parser.add_argument("--creation-coverage-output", default=str(DEFAULT_ROOT / "crypto_creation_time_coverage_report.json"))
    parser.add_argument("--cache-dir", default=str(DEFAULT_ROOT / "binance_klines"))
    parser.add_argument("--metadata-cache-dir", default=str(DEFAULT_ROOT / "gamma_market_metadata"))
    parser.add_argument("--price-history-rows", default=str(DEFAULT_ROOT / "probability_decision_snapshots.jsonl"))
    parser.add_argument("--fetch-gamma-metadata", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--max-markets", type=int, default=0)
    return parser.parse_args(argv)


def _metadata_cache_path(cache_dir: str | Path, market: dict) -> Path:
    identifier = str(market.get("market_id") or market.get("market_slug") or "missing")
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in identifier)
    return Path(cache_dir) / f"{safe}.json"


def _fetch_gamma_metadata(market: dict, *, cache_dir: str | Path, client: PolymarketReadonlyClient) -> tuple[dict, str]:
    cache_path = _metadata_cache_path(cache_dir, market)
    if cache_path.exists():
        try:
            parsed = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict) and parsed:
            return parsed, "cache"
    metadata = {}
    try:
        if market.get("market_id"):
            metadata = client.get_market_by_id(str(market.get("market_id")))
        if not metadata and market.get("market_slug"):
            metadata = client.find_market_by_slug(str(market.get("market_slug"))) or {}
    except PolymarketReadonlyError as exc:
        return {"metadata_error": str(exc)}, "error"
    if metadata:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
        return metadata, "fetched"
    return {}, "missing"


def _earliest_price_history_by_slug(path: str | Path) -> dict[str, str]:
    earliest: dict[str, str] = {}
    for row in load_jsonl(path):
        slug = str(row.get("market_slug") or "")
        timestamp = str(row.get("decision_time") or row.get("timestamp") or "")
        if not slug or not timestamp:
            continue
        if slug not in earliest or timestamp < earliest[slug]:
            earliest[slug] = timestamp
    return earliest


def _count_by(rows: list[dict], key: str) -> list[dict]:
    counts = Counter(str(row.get(key) or "none") for row in rows)
    return [{"reason": reason, "count": counts[reason]} for reason in sorted(counts)]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = []
    creation_rows = []
    metadata_counts: Counter[str] = Counter()
    price_history_by_slug = _earliest_price_history_by_slug(args.price_history_rows)
    client = PolymarketReadonlyClient()
    for market in load_jsonl(args.active_markets):
        if not isinstance(market, dict) or not market.get("active"):
            continue
        parsed = parse_crypto_threshold_market(market)
        if parsed is None:
            continue
        semantics = classify_crypto_semantics(market)
        if semantics != "touch_barrier":
            continue
        metadata = {}
        if args.fetch_gamma_metadata:
            metadata, metadata_source = _fetch_gamma_metadata(market, cache_dir=args.metadata_cache_dir, client=client)
            metadata_counts[metadata_source] += 1
        enriched = merge_market_metadata(market, metadata)
        if not enriched.get("earliest_price_history_timestamp") and price_history_by_slug.get(str(enriched.get("market_slug"))):
            enriched["earliest_price_history_timestamp"] = price_history_by_slug[str(enriched.get("market_slug"))]
        creation = resolve_market_creation_time(
            enriched,
            generated_at=generated_at,
            end_time=parsed.get("target_time") or enriched.get("end_time"),
            allow_proxy=True,
        )
        start_time = creation.get("market_creation_time")
        creation_row = {
            "market_slug": enriched.get("market_slug"),
            "market_id": enriched.get("market_id"),
            "asset": parsed.get("asset"),
            "threshold": parsed.get("threshold"),
            "target_time": parsed.get("target_time") or enriched.get("end_time"),
            "market_creation_time": start_time,
            "creation_time_source": creation.get("creation_time_source"),
            "creation_time_proxy": creation.get("creation_time_proxy"),
            "can_generate_official_candidate": creation.get("can_generate_official_candidate"),
            "can_generate_shadow_watch": creation.get("can_generate_shadow_watch"),
            "gap_reason": creation.get("gap_reason"),
            "metadata_missing_reason": creation.get("metadata_missing_reason"),
            "metadata_missing_fields": creation.get("metadata_missing_fields"),
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        creation_rows.append(creation_row)
        if not start_time:
            row = {
                "market_slug": enriched.get("market_slug"),
                "asset": parsed.get("asset"),
                "pair": None,
                "threshold": parsed.get("threshold"),
                "market_creation_time": None,
                "creation_time_source": creation.get("creation_time_source"),
                "creation_time_proxy": creation.get("creation_time_proxy"),
                "can_generate_official_candidate": creation.get("can_generate_official_candidate"),
                "can_generate_shadow_watch": creation.get("can_generate_shadow_watch"),
                "verification_start_time": None,
                "verification_end_time": generated_at,
                "max_high_since_start": None,
                "max_high_at": None,
                "barrier_already_touched": False,
                "high_since_start_verified": False,
                "kline_count": 0,
                "gap_reason": creation.get("gap_reason") or "start_time_unverified",
                "metadata_missing_reason": creation.get("metadata_missing_reason"),
                "data_source": "binance_1m_klines",
                "paper_only": True,
                "live_order_path": False,
            }
        else:
            row = verify_high_since_start(
                asset=str(parsed.get("asset")),
                threshold=float(parsed.get("threshold")),
                market_creation_time=start_time,
                current_time=generated_at,
                cache_dir=args.cache_dir,
            )
            row.update(
                {
                    "market_slug": enriched.get("market_slug"),
                    "threshold": parsed.get("threshold"),
                    "market_creation_time": start_time,
                    "creation_time_source": creation.get("creation_time_source"),
                    "creation_time_proxy": creation.get("creation_time_proxy"),
                    "can_generate_official_candidate": creation.get("can_generate_official_candidate"),
                    "can_generate_shadow_watch": creation.get("can_generate_shadow_watch"),
                }
            )
        rows.append(row)
        if int(args.max_markets) > 0 and len(rows) >= int(args.max_markets):
            break
    summary = summarize_high_since_start_rows(rows)
    report = {
        "schema_version": "polyweather_polymarket_alpha_crypto_high_since_start_report.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        **summary,
        "start_time_unverified_count": len([row for row in rows if row.get("gap_reason") == "start_time_unverified"]),
        "rows": rows,
    }
    creation_report = {
        "schema_version": "polyweather_polymarket_alpha_crypto_creation_time_coverage.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "touch_barrier_market_count": len(creation_rows),
        "official_creation_time_count": len([row for row in creation_rows if row.get("market_creation_time") and not row.get("creation_time_proxy")]),
        "proxy_creation_time_count": len([row for row in creation_rows if row.get("market_creation_time") and row.get("creation_time_proxy")]),
        "start_time_unverified_count": len([row for row in creation_rows if not row.get("market_creation_time")]),
        "creation_time_source_counts": _count_by(creation_rows, "creation_time_source"),
        "gap_reason_counts": _count_by(creation_rows, "gap_reason"),
        "metadata_fetch_counts": [{"reason": key, "count": metadata_counts[key]} for key in sorted(metadata_counts)],
        "rows": creation_rows,
    }
    write_json(args.creation_coverage_output, creation_report)
    write_json(args.summary_output, report)
    print(json.dumps({key: report.get(key) for key in (
        "touch_barrier_market_count",
        "high_since_start_verified_count",
        "barrier_already_touched_count",
        "verified_not_touched_count",
        "live_order_path",
    )}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
