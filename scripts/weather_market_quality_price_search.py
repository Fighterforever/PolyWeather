#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_quality_surface import (  # noqa: E402
    build_price_conditioned_quality_search,
    dump_quality_surface_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find current quality-surface weather opportunities conditioned on conservative closed base-rates.",
    )
    parser.add_argument("--signal-report", required=True)
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--allowed-side", action="append", dest="allowed_sides")
    parser.add_argument("--exclude-bucket-type", action="append", dest="excluded_bucket_types")
    parser.add_argument("--min-price", type=float, default=0.03)
    parser.add_argument("--max-price", type=float, default=0.85)
    parser.add_argument("--max-spread", type=float, default=0.02)
    parser.add_argument("--min-liquidity", type=float, default=10.0)
    parser.add_argument("--min-bid-depth-usdc-3c", type=float, default=10.0)
    parser.add_argument("--min-ask-depth-usdc-3c", type=float, default=10.0)
    parser.add_argument("--base-rate-prior-count", type=int, default=20)
    parser.add_argument("--base-rate-haircut", type=float, default=0.20)
    parser.add_argument("--min-base-rate-count", type=int, default=10)
    parser.add_argument("--min-conservative-base-rate", type=float, default=0.55)
    parser.add_argument("--min-discount-cents", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    signal_report = json.loads(Path(args.signal_report).read_text(encoding="utf-8"))
    report = build_price_conditioned_quality_search(
        signal_report,
        backfill_dir=args.backfill_dir,
        allowed_sides=args.allowed_sides or (),
        excluded_bucket_types=args.excluded_bucket_types or ("eq",),
        min_price=args.min_price,
        max_price=args.max_price,
        max_spread=args.max_spread,
        min_liquidity=args.min_liquidity,
        min_bid_depth_usdc_3c=args.min_bid_depth_usdc_3c,
        min_ask_depth_usdc_3c=args.min_ask_depth_usdc_3c,
        base_rate_prior_count=args.base_rate_prior_count,
        base_rate_haircut=args.base_rate_haircut,
        min_base_rate_count=args.min_base_rate_count,
        min_conservative_base_rate=args.min_conservative_base_rate,
        min_discount_cents=args.min_discount_cents,
    )
    print(dump_quality_surface_report(report))


if __name__ == "__main__":
    main()
