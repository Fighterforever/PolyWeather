#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_maker_quote_journal import (  # noqa: E402
    markout_open_maker_quotes,
    summarize_maker_quote_journal,
    write_maker_quote_journal_from_fills,
)
from src.trading.weather_paper_journal import (  # noqa: E402
    markout_open_paper_fills,
    summarize_paper_journal,
    utc_now_iso,
    write_paper_journal,
)
from src.trading.weather_quality_surface import (  # noqa: E402
    DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR,
    build_price_conditioned_paper_report,
    build_price_conditioned_quality_search,
    build_price_conditioned_validation_report,
)
from src.trading.weather_resolved_audit import audit_paper_fills_resolution  # noqa: E402


SCHEMA_VERSION = "polyweather_weather_quality_price_cycle.v1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write price-conditioned Polymarket weather opportunities to a dedicated paper-only journal.",
    )
    parser.add_argument("--signal-report", required=True)
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PRICE_CONDITIONED_JOURNAL_DIR))
    parser.add_argument("--paper-journal-profile", default="price-conditioned")
    parser.add_argument("--paper-max-fills", type=int, default=30)
    parser.add_argument(
        "--sample-interval-minutes",
        type=float,
        default=60.0,
        help="Minimum minutes before the same price-conditioned market/side can be sampled again.",
    )
    parser.add_argument("--markout-max-fills", type=int, default=100)
    parser.add_argument("--audit-max-fills", type=int, default=100)
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
    parser.add_argument(
        "--write-maker-quotes",
        action="store_true",
        help="Create and mark paper-only maker-bid shadow quotes for the dedicated journal.",
    )
    parser.add_argument("--maker-quote-size", type=float, default=1.0)
    parser.add_argument("--maker-max-quotes", type=int, default=100)
    parser.add_argument("--maker-markout-max-quotes", type=int, default=100)
    return parser.parse_args(argv)


def _without_records(payload: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if payload is None:
        return None
    return {key: value for key, value in payload.items() if key != "records"}


def build_cycle(args: argparse.Namespace) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    signal_report = json.loads(Path(args.signal_report).read_text(encoding="utf-8"))
    price_search = build_price_conditioned_quality_search(
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
        generated_at=generated_at,
    )
    paper_report = build_price_conditioned_paper_report(price_search, generated_at=generated_at)
    paper_journal = write_paper_journal(
        paper_report,
        journal_dir=args.paper_journal_dir,
        profile=args.paper_journal_profile,
        include_candidates=True,
        include_watch=True,
        include_quarantine=True,
        max_fills=args.paper_max_fills,
        recorded_at=generated_at,
        min_reentry_seconds=int(max(0.0, float(args.sample_interval_minutes)) * 60),
    )
    markout = markout_open_paper_fills(
        journal_dir=args.paper_journal_dir,
        max_fills=args.markout_max_fills,
        recorded_at=generated_at,
    )
    resolved_audit = audit_paper_fills_resolution(
        journal_dir=args.paper_journal_dir,
        max_fills=args.audit_max_fills,
        include_unresolved=True,
        recorded_at=generated_at,
    )
    maker_quote_journal = None
    maker_quote_markout = None
    maker_quote_summary = None
    if bool(args.write_maker_quotes):
        maker_quote_journal = write_maker_quote_journal_from_fills(
            journal_dir=args.paper_journal_dir,
            quote_size=float(args.maker_quote_size),
            max_quotes=args.maker_max_quotes,
            recorded_at=generated_at,
        )
        maker_quote_markout = markout_open_maker_quotes(
            journal_dir=args.paper_journal_dir,
            max_quotes=args.maker_markout_max_quotes,
            recorded_at=generated_at,
        )
        maker_quote_summary = summarize_maker_quote_journal(args.paper_journal_dir)
    validation = build_price_conditioned_validation_report(journal_dir=args.paper_journal_dir)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "live_order_path": False,
        "counts_for_live_gate": False,
        "price_search": {
            key: value
            for key, value in price_search.items()
            if key != "opportunities"
        },
        "paper_report_summary": paper_report.get("summary"),
        "paper_journal": paper_journal,
        "paper_journal_summary": summarize_paper_journal(args.paper_journal_dir),
        "markout": _without_records(markout),
        "resolved_audit": _without_records(resolved_audit),
        "maker_quote_journal": maker_quote_journal,
        "maker_quote_markout": maker_quote_markout,
        "maker_quote_summary": maker_quote_summary,
        "validation": validation,
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    print(json.dumps(build_cycle(args), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
