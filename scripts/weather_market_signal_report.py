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

from src.trading.weather_market_signal import (  # noqa: E402
    WeatherMarketSignalConfig,
    build_weather_market_signal_report,
)
from src.trading.weather_paper_journal import (  # noqa: E402
    DEFAULT_PAPER_JOURNAL_DIR,
    write_paper_journal,
)
from src.trading.polyweather_model_rows import (  # noqa: E402
    build_analysis_model_payload_for_targets,
    merge_scan_model_payloads,
)
from src.trading.weather_market_enrichment import (  # noqa: E402
    enrich_polymarket_payload_with_scan_models,
    temperature_model_targets_from_payload,
)
from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_signal_risk_filter import (  # noqa: E402
    build_risk_rules_from_journal,
    load_risk_rules_from_markout_strata,
)


def build_scan_terminal_payload(filters: Dict[str, Any], *, force_refresh: bool = False) -> Dict[str, Any]:
    from loguru import logger

    logger.disable("src")
    logger.disable("web")
    try:
        from web.scan_terminal_service import build_scan_terminal_payload as _build_scan_terminal_payload

        return _build_scan_terminal_payload(filters, force_refresh=force_refresh)
    finally:
        logger.enable("src")
        logger.enable("web")


def _json_arg(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON value must be an object")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a paper-only weather market signal report.",
    )
    parser.add_argument(
        "--source",
        choices=("scan-terminal", "polymarket"),
        default="scan-terminal",
        help="Input source for rows. Polymarket mode is read-only and never submits orders.",
    )
    parser.add_argument(
        "--filters-json",
        type=_json_arg,
        default={},
        help='Scan filters as JSON, for example {"limit": 25, "min_edge_pct": 2}.',
    )
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--min-edge-percent", type=float, default=5.0)
    parser.add_argument("--min-liquidity", type=float, default=500.0)
    parser.add_argument("--min-model-probability", type=float, default=0.08)
    parser.add_argument("--min-price", type=float, default=0.03)
    parser.add_argument("--max-price", type=float, default=0.85)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument(
        "--paper-tail-profile",
        action="store_true",
        help="Paper-only tail-edge profile: allows very cheap temperature tails and incomplete thin books.",
    )
    parser.add_argument(
        "--tight-exploration-profile",
        action="store_true",
        help="Paper-only discovery profile: cheap tails allowed, but spread must be tight and order book present.",
    )
    parser.add_argument(
        "--quality-surface-profile",
        action="store_true",
        help="Paper-only discovery profile: exclude eq/very cheap tails and require deeper two-sided books.",
    )
    parser.add_argument(
        "--allowed-side",
        action="append",
        dest="allowed_sides",
        help="Restrict candidate assessment to this side, for example yes or no. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-bucket-type",
        action="append",
        dest="excluded_bucket_types",
        help="Exclude bucket types such as eq, range, ge, or le. Can be repeated.",
    )
    parser.add_argument("--min-bid-depth-usdc-3c", type=float, default=0.0)
    parser.add_argument("--min-ask-depth-usdc-3c", type=float, default=0.0)
    parser.add_argument(
        "--polymarket-query",
        action="append",
        dest="polymarket_queries",
        help="Polymarket public-search query. Can be repeated.",
    )
    parser.add_argument("--polymarket-row-limit", type=int, default=50)
    parser.add_argument("--polymarket-search-limit", type=int, default=25)
    parser.add_argument("--polymarket-city-search-limit", type=int, default=5)
    parser.add_argument("--max-city-temperature-queries", type=int, default=None)
    parser.add_argument("--polymarket-active-scan-limit", type=int, default=500)
    parser.add_argument(
        "--no-city-temperature-queries",
        action="store_true",
        help="Disable monitored-city Polymarket searches such as 'highest temperature in Seoul'.",
    )
    parser.add_argument(
        "--join-scan-models",
        action="store_true",
        help="Join Polymarket rows to local scan-terminal model distributions and compute edge.",
    )
    parser.add_argument(
        "--no-analysis-fallback",
        action="store_true",
        help="When joining models, do not call PolyWeather analysis for parsed city/date targets missing from scan-terminal.",
    )
    parser.add_argument(
        "--scan-model-filters-json",
        type=_json_arg,
        default={"time_range": "today", "limit": 200},
        help="Scan-terminal filters used only with --join-scan-models.",
    )
    parser.add_argument(
        "--no-polymarket-order-books",
        action="store_true",
        help="Skip CLOB /book reads and use Gamma outcome prices only.",
    )
    parser.add_argument(
        "--include-expired-polymarket-markets",
        action="store_true",
        help="Do not skip markets whose endDate has already passed. Intended for diagnostics only.",
    )
    parser.add_argument(
        "--include-not-accepting-polymarket-orders",
        action="store_true",
        help="Do not skip markets with acceptingOrders=false. Intended for diagnostics only.",
    )
    parser.add_argument(
        "--allow-incomplete-order-book",
        action="store_true",
        help="Do not reject rows solely because spread/order-book fields are missing.",
    )
    parser.add_argument(
        "--write-paper-journal",
        action="store_true",
        help="Persist this paper-only report snapshot and candidate fills to a local journal.",
    )
    parser.add_argument(
        "--paper-journal-dir",
        default=str(DEFAULT_PAPER_JOURNAL_DIR),
        help="Directory for paper snapshots, fills, and manifest.",
    )
    parser.add_argument("--paper-journal-profile", default="default")
    parser.add_argument(
        "--paper-include-watch",
        action="store_true",
        help="Also append watch rows to paper_fills.jsonl. Candidate rows are always included.",
    )
    parser.add_argument(
        "--paper-include-quarantine",
        action="store_true",
        help="Also append risk-only quarantine rows. Use a separate journal dir for clean live-readiness evidence.",
    )
    parser.add_argument("--paper-max-fills", type=int, default=None)
    parser.add_argument(
        "--risk-rules-json",
        default=None,
        help="Optional JSON summary from weather_market_markout_strata.py containing do_not_live_rules.",
    )
    parser.add_argument(
        "--apply-markout-risk-rules",
        action="store_true",
        help="Build do-not-live candidate filters from the current paper journal markout strata.",
    )
    parser.add_argument(
        "--risk-rule-min-count",
        type=int,
        default=1,
        help="Minimum marked observations required before a negative stratum becomes a risk rule.",
    )
    parser.add_argument(
        "--apply-quarantine-surface-risk-rules",
        action="store_true",
        help="Also build cooldown risk rules from negative quarantine surface strata.",
    )
    parser.add_argument("--quarantine-journal-dir", default="data/trading/weather_quarantine_paper")
    parser.add_argument("--quarantine-surface-min-decision-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-promote-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--quarantine-surface-min-win-rate", type=float, default=0.55)
    parser.add_argument("--quarantine-surface-min-maker-quote-count", type=int, default=0)
    parser.add_argument("--quarantine-surface-min-maker-mean-markout-cents", type=float, default=0.0)
    parser.add_argument(
        "--risk-rule-mode",
        choices=("live", "exploration", "off"),
        default="live",
        help="live applies all negative markout rules; exploration keeps only narrower discovery guards.",
    )
    parser.add_argument(
        "--suppress-saturated-broad-risk-rules",
        action="store_true",
        help="After the first assessment, suppress broad risk rules that cover most rows and rerun assessment.",
    )
    parser.add_argument(
        "--suppress-saturated-partition-risk-rules",
        action="store_true",
        help="Suppress non-specific risk-rule groups whose members collectively cover most rows and rerun assessment.",
    )
    parser.add_argument("--saturated-risk-rule-min-coverage", type=float, default=0.80)
    parser.add_argument("--partition-saturated-risk-rule-min-coverage", type=float, default=0.80)
    parser.add_argument(
        "--include-assessments",
        action="store_true",
        help="Include every assessed row in the JSON report for downstream diagnostics.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.source == "polymarket":
        payload = build_polymarket_weather_payload(
            queries=args.polymarket_queries
            or ("temperature", "rain", "hurricane", "air quality"),
            row_limit=max(1, int(args.polymarket_row_limit)),
            search_limit_per_query=max(1, int(args.polymarket_search_limit)),
            include_city_temperature_queries=not bool(args.no_city_temperature_queries),
            city_search_limit_per_query=max(1, int(args.polymarket_city_search_limit)),
            max_city_temperature_queries=(
                max(0, int(args.max_city_temperature_queries))
                if args.max_city_temperature_queries is not None
                else None
            ),
            active_scan_limit=max(1, int(args.polymarket_active_scan_limit)),
            include_order_books=not bool(args.no_polymarket_order_books),
            exclude_expired_markets=not bool(args.include_expired_polymarket_markets),
            exclude_not_accepting_orders=not bool(args.include_not_accepting_polymarket_orders),
        )
        if args.join_scan_models:
            scan_payload = build_scan_terminal_payload(
                args.scan_model_filters_json,
                force_refresh=bool(args.force_refresh),
            )
            targets = temperature_model_targets_from_payload(payload)
            if targets and not bool(args.no_analysis_fallback):
                fallback_payload = build_analysis_model_payload_for_targets(
                    targets,
                    force_refresh=bool(args.force_refresh),
                    detail_mode="panel",
                )
                scan_payload = merge_scan_model_payloads(scan_payload, fallback_payload)
            payload = enrich_polymarket_payload_with_scan_models(payload, scan_payload)
    else:
        payload = build_scan_terminal_payload(
            args.filters_json,
            force_refresh=bool(args.force_refresh),
        )
    min_price = float(args.min_price)
    min_liquidity = float(args.min_liquidity)
    max_spread = float(args.max_spread)
    require_order_book = not bool(args.allow_incomplete_order_book)
    if args.paper_tail_profile:
        min_price = 0.001
        min_liquidity = 0.0
        max_spread = max(max_spread, 0.10)
        require_order_book = False
    if args.tight_exploration_profile:
        min_price = 0.001
        min_liquidity = 0.0
        max_spread = min(max_spread, 0.005)
        require_order_book = True
    excluded_bucket_types = tuple(str(value).strip().lower() for value in args.excluded_bucket_types or [] if str(value).strip())
    allowed_sides = tuple(str(value).strip().lower() for value in args.allowed_sides or [] if str(value).strip())
    min_bid_depth = float(args.min_bid_depth_usdc_3c)
    min_ask_depth = float(args.min_ask_depth_usdc_3c)
    if args.quality_surface_profile:
        min_price = max(min_price, 0.03)
        max_spread = min(max_spread, 0.02)
        min_liquidity = 10.0 if float(args.min_liquidity) == 500.0 else max(min_liquidity, 10.0)
        min_bid_depth = max(min_bid_depth, 10.0)
        min_ask_depth = max(min_ask_depth, 10.0)
        require_order_book = True
        excluded_bucket_types = tuple(sorted(set(excluded_bucket_types) | {"eq"}))

    config = WeatherMarketSignalConfig(
        min_edge_percent=float(args.min_edge_percent),
        min_liquidity=min_liquidity,
        min_model_probability=float(args.min_model_probability),
        min_price=min_price,
        max_price=float(args.max_price),
        max_spread=max_spread,
        max_candidates=max(1, int(args.max_candidates)),
        require_order_book=require_order_book,
        allowed_sides=allowed_sides,
        excluded_bucket_types=excluded_bucket_types,
        min_bid_depth_usdc_3c=min_bid_depth,
        min_ask_depth_usdc_3c=min_ask_depth,
        suppress_saturated_broad_risk_rules=bool(args.suppress_saturated_broad_risk_rules),
        suppress_saturated_partition_risk_rules=bool(args.suppress_saturated_partition_risk_rules),
        saturated_risk_rule_min_coverage=float(args.saturated_risk_rule_min_coverage),
        partition_saturated_risk_rule_min_coverage=float(args.partition_saturated_risk_rule_min_coverage),
    )
    risk_rules = []
    if args.risk_rules_json:
        risk_rules.extend(load_risk_rules_from_markout_strata(args.risk_rules_json))
    if args.apply_markout_risk_rules or args.apply_quarantine_surface_risk_rules:
        risk_rules.extend(
            (
                build_risk_rules_from_journal(
                    journal_dir=args.paper_journal_dir,
                    min_count=max(1, int(args.risk_rule_min_count)),
                    include_quarantine_surface_rules=bool(args.apply_quarantine_surface_risk_rules),
                    quarantine_journal_dir=args.quarantine_journal_dir,
                    quarantine_surface_min_decision_count=args.quarantine_surface_min_decision_count,
                    quarantine_surface_min_promote_count=args.quarantine_surface_min_promote_count,
                    quarantine_surface_min_mean_markout_cents=args.quarantine_surface_min_mean_markout_cents,
                    quarantine_surface_min_win_rate=args.quarantine_surface_min_win_rate,
                    quarantine_surface_min_maker_quote_count=args.quarantine_surface_min_maker_quote_count,
                    quarantine_surface_min_maker_mean_markout_cents=args.quarantine_surface_min_maker_mean_markout_cents,
                ).get("rules")
                or []
            )
        )
    report = build_weather_market_signal_report(
        payload,
        config=config,
        risk_rules=risk_rules,
        risk_rule_mode=args.risk_rule_mode,
        include_assessments=bool(args.include_assessments),
    )
    if args.write_paper_journal:
        report["paper_journal"] = write_paper_journal(
            report,
            journal_dir=args.paper_journal_dir,
            profile=args.paper_journal_profile,
            include_watch=bool(args.paper_include_watch),
            include_quarantine=bool(args.paper_include_quarantine),
            max_fills=args.paper_max_fills,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
