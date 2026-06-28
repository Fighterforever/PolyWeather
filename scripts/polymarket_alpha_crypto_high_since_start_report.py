#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
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
from src.trading.polymarket_alpha.crypto_market_semantics import classify_crypto_semantics, parse_start_time  # noqa: E402
from src.trading.polymarket_alpha.crypto_probability_model import load_jsonl, parse_crypto_threshold_market  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify crypto touch-barrier markets using Binance 1m high prices.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_high_since_start_report.json"))
    parser.add_argument("--cache-dir", default=str(DEFAULT_ROOT / "binance_klines"))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--max-markets", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = []
    for market in load_jsonl(args.active_markets):
        if not isinstance(market, dict) or not market.get("active"):
            continue
        parsed = parse_crypto_threshold_market(market)
        if parsed is None:
            continue
        semantics = classify_crypto_semantics(market)
        if semantics != "touch_barrier":
            continue
        start_time = parse_start_time(market, generated_at=generated_at, end_time=parsed.get("target_time") or market.get("end_time"))
        if not start_time:
            row = {
                "market_slug": market.get("market_slug"),
                "asset": parsed.get("asset"),
                "pair": None,
                "threshold": parsed.get("threshold"),
                "market_creation_time": None,
                "verification_start_time": None,
                "verification_end_time": generated_at,
                "max_high_since_start": None,
                "max_high_at": None,
                "barrier_already_touched": False,
                "high_since_start_verified": False,
                "kline_count": 0,
                "gap_reason": "start_time_unverified",
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
                    "market_slug": market.get("market_slug"),
                    "threshold": parsed.get("threshold"),
                    "market_creation_time": start_time,
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
        "rows": rows,
    }
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
