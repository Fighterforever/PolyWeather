#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.weather_market_signal_report import build_scan_terminal_payload  # noqa: E402
from src.trading.polymarket_readonly import DEFAULT_WEATHER_QUERIES, build_polymarket_weather_payload  # noqa: E402
from src.trading.polyweather_model_rows import (  # noqa: E402
    build_analysis_model_payload_for_targets,
    merge_scan_model_payloads,
)
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_execution_calibration import (  # noqa: E402
    DEFAULT_MAKER_FOCUS_JOURNAL_DIR,
    build_maker_focus_signal_report,
    summarize_execution_calibration,
)
from src.trading.weather_live_readiness import build_live_readiness_report  # noqa: E402
from src.trading.weather_maker_quote_journal import (  # noqa: E402
    markout_open_maker_quotes,
    summarize_maker_quote_journal,
    write_maker_quote_journal_from_fills,
)
from src.trading.weather_maker_quote_blocker_calibration import (  # noqa: E402
    build_maker_quote_blocker_calibration_report,
)
from src.trading.weather_market_enrichment import (  # noqa: E402
    enrich_polymarket_payload_with_scan_models,
    temperature_model_targets_from_payload,
)
from src.trading.weather_market_signal import (  # noqa: E402
    WeatherMarketSignalConfig,
    build_weather_market_signal_report,
)
from src.trading.weather_model_coverage import build_weather_model_coverage_report  # noqa: E402
from src.trading.weather_paper_journal import (  # noqa: E402
    DEFAULT_PAPER_JOURNAL_DIR,
    markout_open_paper_fills,
    utc_now_iso,
    write_paper_journal,
)
from src.trading.weather_quarantine_validation import build_quarantine_validation_report  # noqa: E402
from src.trading.weather_quarantine_surface import build_quarantine_surface_report  # noqa: E402
from src.trading.weather_quality_surface import (  # noqa: E402
    build_quality_threshold_calibration_report,
    build_quarantine_promotion_report,
)
from src.trading.weather_resolved_audit import (  # noqa: E402
    audit_paper_fills_resolution,
    build_resolved_gap_report,
)
from src.trading.weather_signal_risk_filter import build_risk_rules_from_journal  # noqa: E402
from src.trading.weather_targeted_shadow import (  # noqa: E402
    DEFAULT_CURRENT_SIGNAL_TAKER_JOURNAL_DIR,
    DEFAULT_TARGETED_SHADOW_JOURNAL_DIR,
    build_current_signal_taker_probe_report,
    build_current_signal_taker_validation_report,
    build_targeted_shadow_cooldown_report,
    build_targeted_shadow_signal_report,
    build_targeted_shadow_validation_report,
)
from src.trading.weather_temperature_execution_experiment import (  # noqa: E402
    DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR,
    DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR,
    build_temperature_execution_experiment_report,
    run_temperature_taker_paper_cycle,
    write_temperature_execution_quotes,
)
from src.trading.weather_temperature_opportunity import build_temperature_opportunity_report  # noqa: E402


SCHEMA_VERSION = "polyweather_weather_paper_cycle.v1"
DEFAULT_QUARANTINE_JOURNAL_DIR = Path("data/trading/weather_quarantine_paper")
DEFAULT_EQ_SHADOW_JOURNAL_DIR = Path("data/trading/weather_eq_shadow_paper")


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


@contextmanager
def _collector_patch_env(enable_collector_patch: bool):
    if enable_collector_patch:
        yield
        return
    env_key = "POLYWEATHER_COLLECTOR_PATCH_ENDPOINT"
    previous = os.environ.get(env_key)
    os.environ[env_key] = ""
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = previous


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one paper-only Polymarket weather evidence cycle.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--paper-journal-profile", default="paper-cycle")
    parser.add_argument("--paper-max-fills", type=int, default=10)
    parser.add_argument("--paper-include-watch", action="store_true")
    parser.add_argument(
        "--write-quarantine-journal",
        action="store_true",
        help="Persist risk-only rejected rows to a separate paper journal for diagnostics.",
    )
    parser.add_argument(
        "--no-quarantine-journal",
        action="store_true",
        help="With --production-profile, do not persist quarantine near-miss paper diagnostics.",
    )
    parser.add_argument("--quarantine-journal-dir", default=str(DEFAULT_QUARANTINE_JOURNAL_DIR))
    parser.add_argument("--quarantine-max-fills", type=int, default=30)
    parser.add_argument("--markout-max-fills", type=int, default=100)
    parser.add_argument("--markout-min-interval-seconds", type=float, default=0.0)
    parser.add_argument("--quarantine-markout-max-fills", type=int, default=100)
    parser.add_argument("--audit-max-fills", type=int, default=100)
    parser.add_argument("--quarantine-audit-max-fills", type=int, default=100)
    parser.add_argument("--quarantine-min-unlock-count", type=int, default=5)
    parser.add_argument("--quarantine-min-maker-inferred-fills", type=int, default=3)
    parser.add_argument("--quarantine-min-unlock-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--quarantine-min-unlock-win-rate", type=float, default=0.55)
    parser.add_argument("--quarantine-surface-min-decision-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-promote-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--quarantine-surface-min-win-rate", type=float, default=0.55)
    parser.add_argument("--quarantine-surface-min-maker-quote-count", type=int, default=0)
    parser.add_argument("--quarantine-surface-min-maker-mean-markout-cents", type=float, default=0.0)
    parser.add_argument(
        "--apply-quarantine-surface-risk-rules",
        action="store_true",
        help="Use negative quarantine surface strata as paper-only cooldown risk rules.",
    )
    parser.add_argument(
        "--no-quarantine-surface-risk-rules",
        action="store_true",
        help="With --production-profile, do not add quarantine surface cooldown risk rules.",
    )
    parser.add_argument("--targeted-shadow-journal-dir", default=str(DEFAULT_TARGETED_SHADOW_JOURNAL_DIR))
    parser.add_argument("--write-targeted-shadow-journal", action="store_true")
    parser.add_argument("--no-targeted-shadow-journal", action="store_true")
    parser.add_argument("--targeted-shadow-max-fills", type=int, default=10)
    parser.add_argument("--targeted-shadow-markout-max-fills", type=int, default=100)
    parser.add_argument("--targeted-shadow-audit-max-fills", type=int, default=100)
    parser.add_argument("--targeted-shadow-quarantine-reason", action="append", dest="targeted_shadow_quarantine_reasons")
    parser.add_argument("--targeted-shadow-bucket-type", action="append", dest="targeted_shadow_bucket_types")
    parser.add_argument("--targeted-shadow-near-miss-category", action="append", dest="targeted_shadow_near_miss_categories")
    parser.add_argument("--targeted-shadow-min-edge-percent", type=float, default=5.0)
    parser.add_argument("--no-targeted-shadow-cooldown", action="store_true")
    parser.add_argument("--targeted-shadow-cooldown-min-marked-count", type=int, default=2)
    parser.add_argument("--targeted-shadow-cooldown-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--targeted-shadow-cooldown-max-win-rate", type=float, default=0.5)
    parser.add_argument("--targeted-shadow-include-negative-edge", action="store_true")
    parser.add_argument("--current-signal-taker-journal-dir", default=str(DEFAULT_CURRENT_SIGNAL_TAKER_JOURNAL_DIR))
    parser.add_argument("--write-current-signal-taker-paper", action="store_true")
    parser.add_argument("--no-current-signal-taker-paper", action="store_true")
    parser.add_argument("--current-signal-taker-max-fills", type=int, default=20)
    parser.add_argument("--current-signal-taker-min-edge-percent", type=float, default=5.0)
    parser.add_argument("--current-signal-taker-max-spread", type=float, default=0.03)
    parser.add_argument(
        "--current-signal-taker-allowed-bucket-type",
        action="append",
        dest="current_signal_taker_allowed_bucket_types",
        help=(
            "Restrict current-signal taker paper probes to specific bucket types. "
            "Production profile defaults to tail threshold buckets: le/ge."
        ),
    )
    parser.add_argument("--current-signal-taker-min-reentry-seconds", type=int, default=3600)
    parser.add_argument("--current-signal-taker-markout-max-fills", type=int, default=100)
    parser.add_argument("--current-signal-taker-audit-max-fills", type=int, default=100)
    parser.add_argument("--current-signal-taker-validation-min-marked-count", type=int, default=10)
    parser.add_argument("--current-signal-taker-validation-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--current-signal-taker-validation-min-win-rate", type=float, default=0.55)
    parser.add_argument(
        "--current-signal-taker-validation-required-horizon",
        action="append",
        dest="current_signal_taker_validation_required_horizons",
    )
    parser.add_argument("--current-signal-taker-validation-min-horizon-count", type=int, default=3)
    parser.add_argument("--current-signal-taker-validation-min-resolved-count", type=int, default=1)
    parser.add_argument("--eq-shadow-journal-dir", default=str(DEFAULT_EQ_SHADOW_JOURNAL_DIR))
    parser.add_argument("--write-eq-shadow-journal", action="store_true")
    parser.add_argument("--no-eq-shadow-journal", action="store_true")
    parser.add_argument("--eq-shadow-max-fills", type=int, default=10)
    parser.add_argument("--eq-shadow-markout-max-fills", type=int, default=100)
    parser.add_argument("--eq-shadow-audit-max-fills", type=int, default=100)
    parser.add_argument("--eq-shadow-min-edge-percent", type=float, default=5.0)
    parser.add_argument("--no-eq-shadow-cooldown", action="store_true")
    parser.add_argument("--resolved-gap-settlement-grace-hours", type=float, default=24.0)
    parser.add_argument("--calibration-min-marked-count", type=int, default=10)
    parser.add_argument("--calibration-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--calibration-min-win-rate", type=float, default=0.55)
    parser.add_argument(
        "--write-maker-quotes",
        action="store_true",
        help="Create and mark paper-only maker-bid shadow quotes for written journals.",
    )
    parser.add_argument("--maker-quote-size", type=float, default=1.0)
    parser.add_argument(
        "--maker-quote-offset-cents",
        action="append",
        type=float,
        dest="maker_quote_offset_cents",
        help="Add paper maker quotes this many cents below entry bid. Repeat for a ladder; default is 0.",
    )
    parser.add_argument("--maker-max-quotes", type=int, default=100)
    parser.add_argument("--maker-markout-max-quotes", type=int, default=100)
    parser.add_argument("--maker-focus-journal-dir", default=str(DEFAULT_MAKER_FOCUS_JOURNAL_DIR))
    parser.add_argument("--write-maker-focus-journal", action="store_true")
    parser.add_argument("--no-maker-focus-journal", action="store_true")
    parser.add_argument("--maker-focus-max-fills", type=int, default=10)
    parser.add_argument("--maker-focus-markout-max-fills", type=int, default=100)
    parser.add_argument("--maker-focus-audit-max-fills", type=int, default=100)
    parser.add_argument("--maker-focus-group-field", action="append", dest="maker_focus_group_fields")
    parser.add_argument("--maker-focus-min-group-count", type=int, default=3)
    parser.add_argument("--maker-focus-min-group-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--maker-focus-min-group-win-rate", type=float, default=0.55)
    parser.add_argument("--maker-focus-min-edge-percent", type=float, default=5.0)
    parser.add_argument("--maker-focus-ignore-maker-quote-risk-rules", action="store_true")
    parser.add_argument("--maker-blocker-calibration-min-count", type=int, default=3)
    parser.add_argument("--maker-blocker-calibration-min-inferred-fills", type=int, default=3)
    parser.add_argument("--maker-blocker-calibration-min-fill-rate", type=float, default=0.05)
    parser.add_argument("--maker-blocker-calibration-min-mean-maker-markout-cents", type=float, default=0.0)
    parser.add_argument("--maker-blocker-calibration-min-maker-win-rate", type=float, default=0.55)
    parser.add_argument("--model-coverage-max-horizon-days", type=float, default=14.0)
    parser.add_argument("--temperature-opportunity-max-horizon-hours", type=float, default=48.0)
    parser.add_argument("--temperature-opportunity-max-items", type=int, default=20)
    parser.add_argument(
        "--temperature-execution-offset-cents",
        action="append",
        type=float,
        dest="temperature_execution_offset_cents",
        help="Paper-only maker offset ladder for negative-maker temperature opportunities.",
    )
    parser.add_argument("--temperature-execution-min-expected-markout-cents", type=float, default=0.0)
    parser.add_argument("--temperature-execution-min-win-rate", type=float, default=0.55)
    parser.add_argument("--temperature-execution-max-items", type=int, default=20)
    parser.add_argument("--temperature-execution-journal-dir", default=str(DEFAULT_TEMPERATURE_EXECUTION_JOURNAL_DIR))
    parser.add_argument("--write-temperature-execution-quotes", action="store_true")
    parser.add_argument("--no-temperature-execution-quotes", action="store_true")
    parser.add_argument("--temperature-execution-quote-size", type=float, default=1.0)
    parser.add_argument("--temperature-execution-max-quotes", type=int, default=50)
    parser.add_argument("--temperature-execution-markout-max-quotes", type=int, default=50)
    parser.add_argument("--temperature-taker-journal-dir", default=str(DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR))
    parser.add_argument("--write-temperature-taker-paper", action="store_true")
    parser.add_argument("--no-temperature-taker-paper", action="store_true")
    parser.add_argument("--temperature-taker-max-items", type=int, default=20)
    parser.add_argument("--temperature-taker-max-fills", type=int, default=20)
    parser.add_argument("--temperature-taker-markout-max-fills", type=int, default=20)
    parser.add_argument("--temperature-taker-min-reentry-seconds", type=int, default=3600)
    parser.add_argument("--temperature-taker-min-markout-interval-seconds", type=float, default=600.0)
    parser.add_argument("--temperature-taker-validation-min-marked-count", type=int, default=10)
    parser.add_argument("--temperature-taker-validation-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--temperature-taker-validation-min-win-rate", type=float, default=0.55)
    parser.add_argument(
        "--temperature-taker-validation-required-horizon",
        action="append",
        dest="temperature_taker_validation_required_horizons",
    )
    parser.add_argument("--temperature-taker-validation-min-horizon-count", type=int, default=3)
    parser.add_argument("--temperature-taker-validation-min-resolved-count", type=int, default=1)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--live-permission", action="store_true")
    parser.add_argument(
        "--production-profile",
        action="store_true",
        help="Fast live-like paper profile: temperature-only, bounded city scan, quality surface, and markout risk rules.",
    )
    parser.add_argument(
        "--expanded-weather-profile",
        action="store_true",
        help="With production profile, scan all supported weather query families for coverage diagnostics. Non-temperature rows still need a model before they can trade.",
    )

    parser.add_argument("--polymarket-query", action="append", dest="polymarket_queries")
    parser.add_argument("--polymarket-row-limit", type=int, default=80)
    parser.add_argument("--polymarket-search-limit", type=int, default=25)
    parser.add_argument("--polymarket-city-search-limit", type=int, default=5)
    parser.add_argument("--max-city-temperature-queries", type=int, default=None)
    parser.add_argument("--polymarket-active-scan-limit", type=int, default=1)
    parser.add_argument("--no-city-temperature-queries", action="store_true")
    parser.add_argument("--no-polymarket-order-books", action="store_true")
    parser.add_argument(
        "--enable-collector-patch",
        action="store_true",
        help="Allow analysis fallback to POST collector patch events to the web service. Disabled by default for paper cycles.",
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
    parser.add_argument("--no-analysis-fallback", action="store_true")
    parser.add_argument(
        "--scan-model-filters-json",
        type=_json_arg,
        default={"time_range": "today", "limit": 200},
    )

    parser.add_argument("--min-edge-percent", type=float, default=5.0)
    parser.add_argument("--min-model-probability", type=float, default=0.08)
    parser.add_argument("--min-price", type=float, default=0.001)
    parser.add_argument("--max-price", type=float, default=0.85)
    parser.add_argument("--min-liquidity", type=float, default=0.0)
    parser.add_argument("--max-spread", type=float, default=0.10)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--max-quarantine", type=int, default=30)
    parser.add_argument(
        "--quarantine-near-miss-category",
        action="append",
        dest="quarantine_near_miss_categories",
        help=(
            "Also persist single-blocker near misses in the quarantine paper journal for threshold "
            "calibration. Repeat for categories such as edge, spread, price, bucket_type, liquidity, depth."
        ),
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
    parser.add_argument("--allowed-side", action="append", dest="allowed_sides")
    parser.add_argument("--exclude-bucket-type", action="append", dest="excluded_bucket_types")
    parser.add_argument("--min-bid-depth-usdc-3c", type=float, default=0.0)
    parser.add_argument("--min-ask-depth-usdc-3c", type=float, default=0.0)
    parser.add_argument(
        "--apply-markout-risk-rules",
        action="store_true",
        help="Reject candidates matching negative markout strata from the current paper journal.",
    )
    parser.add_argument(
        "--risk-rule-min-count",
        type=int,
        default=1,
        help="Minimum marked observations required before a negative stratum becomes a risk rule.",
    )
    parser.add_argument(
        "--risk-rule-mode",
        choices=("live", "exploration", "off"),
        default="live",
        help="live applies all negative markout rules; exploration keeps only narrower discovery guards.",
    )
    parser.add_argument(
        "--suppress-saturated-broad-risk-rules",
        action="store_true",
        help="After the first scan assessment, suppress broad risk rules that cover most rows and rerun assessment for paper discovery.",
    )
    parser.add_argument(
        "--no-suppress-saturated-broad-risk-rules",
        action="store_true",
        help="With --production-profile, keep even broad saturated risk rules active.",
    )
    parser.add_argument(
        "--suppress-saturated-partition-risk-rules",
        action="store_true",
        help="Suppress non-specific risk-rule groups whose members collectively cover most rows for paper discovery.",
    )
    parser.add_argument(
        "--no-suppress-saturated-partition-risk-rules",
        action="store_true",
        help="With --production-profile, keep partition-saturated risk-rule groups active.",
    )
    parser.add_argument("--saturated-risk-rule-min-coverage", type=float, default=0.80)
    parser.add_argument("--partition-saturated-risk-rule-min-coverage", type=float, default=0.80)
    parser.add_argument(
        "--require-order-book",
        action="store_true",
        help="Reject rows with missing spread/order book fields.",
    )
    return parser.parse_args(argv)


def build_cycle(args: argparse.Namespace) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    production_profile = bool(args.production_profile)
    targeted_shadow_enabled = (
        bool(args.write_targeted_shadow_journal)
        or (production_profile and not bool(args.no_targeted_shadow_journal))
    )
    current_signal_taker_enabled = bool(args.write_current_signal_taker_paper) or (
        production_profile and not bool(args.no_current_signal_taker_paper)
    )
    current_signal_taker_allowed_bucket_types = tuple(
        str(value).strip().lower()
        for value in args.current_signal_taker_allowed_bucket_types or []
        if str(value).strip()
    )
    if production_profile and not current_signal_taker_allowed_bucket_types:
        current_signal_taker_allowed_bucket_types = ("le", "ge")
    eq_shadow_enabled = (
        bool(args.write_eq_shadow_journal)
        or (production_profile and not bool(args.no_eq_shadow_journal))
    )
    maker_focus_enabled = (
        bool(args.write_maker_focus_journal)
        or (production_profile and not bool(args.no_maker_focus_journal))
    )
    quarantine_enabled = (
        bool(args.write_quarantine_journal)
        or (production_profile and not bool(args.no_quarantine_journal))
    )
    quarantine_surface_risk_rules_enabled = bool(args.apply_quarantine_surface_risk_rules) or (
        production_profile and not bool(args.no_quarantine_surface_risk_rules)
    )
    temperature_execution_quotes_enabled = bool(args.write_temperature_execution_quotes) or (
        production_profile and not bool(args.no_temperature_execution_quotes)
    )
    temperature_taker_paper_enabled = bool(args.write_temperature_taker_paper) or (
        production_profile and not bool(args.no_temperature_taker_paper)
    )
    suppress_saturated_broad_risk_rules = bool(args.suppress_saturated_broad_risk_rules) or (
        production_profile and not bool(args.no_suppress_saturated_broad_risk_rules)
    )
    suppress_saturated_partition_risk_rules = bool(args.suppress_saturated_partition_risk_rules) or (
        production_profile and not bool(args.no_suppress_saturated_partition_risk_rules)
    )
    max_city_temperature_queries = (
        max(0, int(args.max_city_temperature_queries))
        if args.max_city_temperature_queries is not None
        else (6 if production_profile else None)
    )
    polymarket_queries = args.polymarket_queries or (
        (
            tuple(DEFAULT_WEATHER_QUERIES)
            if bool(args.expanded_weather_profile)
            else ("temperature",)
        )
        if production_profile
        else tuple(DEFAULT_WEATHER_QUERIES)
    )
    polymarket_row_limit = max(1, int(args.polymarket_row_limit))
    if production_profile:
        polymarket_row_limit = max(polymarket_row_limit, 240)
    payload = build_polymarket_weather_payload(
        queries=polymarket_queries,
        row_limit=polymarket_row_limit,
        search_limit_per_query=max(1, int(args.polymarket_search_limit)),
        include_city_temperature_queries=not bool(args.no_city_temperature_queries),
        city_search_limit_per_query=max(1, int(args.polymarket_city_search_limit)),
        max_city_temperature_queries=max_city_temperature_queries,
        active_scan_limit=max(1, int(args.polymarket_active_scan_limit)),
        include_order_books=not bool(args.no_polymarket_order_books),
        exclude_expired_markets=not bool(args.include_expired_polymarket_markets),
        exclude_not_accepting_orders=not bool(args.include_not_accepting_polymarket_orders),
    )
    with _collector_patch_env(bool(args.enable_collector_patch)):
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

    risk_rules_payload = (
        build_risk_rules_from_journal(
            journal_dir=args.paper_journal_dir,
            min_count=max(1, int(args.risk_rule_min_count)),
            include_quarantine_surface_rules=quarantine_surface_risk_rules_enabled,
            quarantine_journal_dir=args.quarantine_journal_dir,
            quarantine_surface_min_decision_count=args.quarantine_surface_min_decision_count,
            quarantine_surface_min_promote_count=args.quarantine_surface_min_promote_count,
            quarantine_surface_min_mean_markout_cents=args.quarantine_surface_min_mean_markout_cents,
            quarantine_surface_min_win_rate=args.quarantine_surface_min_win_rate,
            quarantine_surface_min_maker_quote_count=args.quarantine_surface_min_maker_quote_count,
            quarantine_surface_min_maker_mean_markout_cents=args.quarantine_surface_min_maker_mean_markout_cents,
        )
        if bool(args.apply_markout_risk_rules) or production_profile
        else {"rule_count": 0, "rules": []}
    )
    min_liquidity = 0.0 if bool(args.tight_exploration_profile) else float(args.min_liquidity)
    min_price = 0.001 if bool(args.tight_exploration_profile) else float(args.min_price)
    max_spread = (
        min(float(args.max_spread), 0.005)
        if bool(args.tight_exploration_profile)
        else float(args.max_spread)
    )
    require_order_book = True if bool(args.tight_exploration_profile) else bool(args.require_order_book)
    excluded_bucket_types = tuple(
        str(value).strip().lower()
        for value in args.excluded_bucket_types or []
        if str(value).strip()
    )
    allowed_sides = tuple(
        str(value).strip().lower()
        for value in args.allowed_sides or []
        if str(value).strip()
    )
    quarantine_near_miss_categories = tuple(
        str(value).strip().lower()
        for value in args.quarantine_near_miss_categories or []
        if str(value).strip()
    )
    if production_profile and not quarantine_near_miss_categories:
        quarantine_near_miss_categories = (
            "edge",
            "spread",
            "price",
            "bucket_type",
            "liquidity",
            "depth",
        )
    min_bid_depth = float(args.min_bid_depth_usdc_3c)
    min_ask_depth = float(args.min_ask_depth_usdc_3c)
    if bool(args.quality_surface_profile) or production_profile:
        min_price = max(min_price, 0.03)
        max_spread = min(max_spread, 0.02)
        min_liquidity = max(min_liquidity, 10.0)
        min_bid_depth = max(min_bid_depth, 10.0)
        min_ask_depth = max(min_ask_depth, 10.0)
        require_order_book = True
        excluded_bucket_types = tuple(sorted(set(excluded_bucket_types) | {"eq"}))
    model_coverage = build_weather_model_coverage_report(
        payload,
        min_price=min_price,
        max_price=float(args.max_price),
        max_spread=max_spread,
        min_liquidity=min_liquidity,
        min_bid_depth_usdc_3c=min_bid_depth,
        min_ask_depth_usdc_3c=min_ask_depth,
        max_model_build_horizon_days=float(args.model_coverage_max_horizon_days),
        generated_at=generated_at,
    )
    signal_report = build_weather_market_signal_report(
        payload,
        config=WeatherMarketSignalConfig(
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
            max_quarantine=max(1, int(args.max_quarantine)),
            quarantine_near_miss_categories=quarantine_near_miss_categories,
            suppress_saturated_broad_risk_rules=bool(suppress_saturated_broad_risk_rules),
            suppress_saturated_partition_risk_rules=bool(suppress_saturated_partition_risk_rules),
            saturated_risk_rule_min_coverage=float(args.saturated_risk_rule_min_coverage),
            partition_saturated_risk_rule_min_coverage=float(args.partition_saturated_risk_rule_min_coverage),
        ),
        risk_rules=risk_rules_payload.get("rules") or [],
        risk_rule_mode=args.risk_rule_mode,
        generated_at=generated_at,
    )
    paper_journal = write_paper_journal(
        signal_report,
        journal_dir=args.paper_journal_dir,
        profile=args.paper_journal_profile,
        include_watch=bool(args.paper_include_watch),
        max_fills=args.paper_max_fills,
        recorded_at=generated_at,
    )
    maker_quote_journal = None
    maker_quote_markout = None
    maker_quote_summary = None
    if bool(args.write_maker_quotes):
        maker_quote_journal = write_maker_quote_journal_from_fills(
            journal_dir=args.paper_journal_dir,
            quote_size=float(args.maker_quote_size),
            quote_offset_cents=args.maker_quote_offset_cents,
            max_quotes=args.maker_max_quotes,
            recorded_at=generated_at,
        )
        maker_quote_markout = markout_open_maker_quotes(
            journal_dir=args.paper_journal_dir,
            max_quotes=args.maker_markout_max_quotes,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        maker_quote_summary = summarize_maker_quote_journal(args.paper_journal_dir)
    quarantine_journal = None
    quarantine_markout = None
    quarantine_resolved_audit = None
    quarantine_maker_quote_journal = None
    quarantine_maker_quote_markout = None
    quarantine_maker_quote_summary = None
    quarantine_validation = None
    quarantine_surface = None
    targeted_shadow_report = None
    targeted_shadow_journal = None
    targeted_shadow_markout = None
    targeted_shadow_resolved_audit = None
    targeted_shadow_maker_quote_journal = None
    targeted_shadow_maker_quote_markout = None
    targeted_shadow_maker_quote_summary = None
    targeted_shadow_validation = None
    targeted_shadow_selection_validation = None
    targeted_shadow_cooldown = None
    current_signal_taker_report = None
    current_signal_taker_journal = None
    current_signal_taker_markout = None
    current_signal_taker_resolved_audit = None
    current_signal_taker_validation = None
    eq_shadow_report = None
    eq_shadow_journal = None
    eq_shadow_markout = None
    eq_shadow_resolved_audit = None
    eq_shadow_maker_quote_journal = None
    eq_shadow_maker_quote_markout = None
    eq_shadow_maker_quote_summary = None
    eq_shadow_validation = None
    eq_shadow_selection_validation = None
    eq_shadow_cooldown = None
    maker_focus_execution_calibration = None
    maker_focus_report = None
    maker_focus_journal = None
    maker_focus_markout = None
    maker_focus_resolved_audit = None
    maker_quote_blocker_calibration = None
    temperature_opportunity = None
    temperature_execution_experiment = None
    temperature_execution_quote_journal = None
    temperature_execution_quote_markout = None
    temperature_execution_quote_summary = None
    temperature_taker_paper = None
    if quarantine_enabled:
        quarantine_journal = write_paper_journal(
            signal_report,
            journal_dir=args.quarantine_journal_dir,
            profile=f"{args.paper_journal_profile}-quarantine",
            include_candidates=False,
            include_watch=False,
            include_quarantine=True,
            max_fills=args.quarantine_max_fills,
            recorded_at=generated_at,
        )
        quarantine_markout = markout_open_paper_fills(
            journal_dir=args.quarantine_journal_dir,
            max_fills=args.quarantine_markout_max_fills,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        quarantine_resolved_audit = audit_paper_fills_resolution(
            journal_dir=args.quarantine_journal_dir,
            backfill_dir=args.backfill_dir,
            max_fills=args.quarantine_audit_max_fills,
            include_unresolved=True,
            recorded_at=generated_at,
        )
        if bool(args.write_maker_quotes):
            quarantine_maker_quote_journal = write_maker_quote_journal_from_fills(
                journal_dir=args.quarantine_journal_dir,
                quote_size=float(args.maker_quote_size),
                quote_offset_cents=args.maker_quote_offset_cents,
                max_quotes=args.maker_max_quotes,
                recorded_at=generated_at,
            )
            quarantine_maker_quote_markout = markout_open_maker_quotes(
                journal_dir=args.quarantine_journal_dir,
                max_quotes=args.maker_markout_max_quotes,
                min_markout_interval_seconds=float(args.markout_min_interval_seconds),
                recorded_at=generated_at,
            )
            quarantine_maker_quote_summary = summarize_maker_quote_journal(args.quarantine_journal_dir)
        quarantine_validation = build_quarantine_validation_report(
            paper_journal_dir=args.paper_journal_dir,
            quarantine_journal_dir=args.quarantine_journal_dir,
            min_unlock_count=args.quarantine_min_unlock_count,
            min_maker_inferred_fills=args.quarantine_min_maker_inferred_fills,
            min_unlock_mean_markout_cents=args.quarantine_min_unlock_mean_markout_cents,
            min_unlock_win_rate=args.quarantine_min_unlock_win_rate,
            generated_at=generated_at,
        )
        quarantine_surface = build_quarantine_surface_report(
            paper_journal_dir=args.paper_journal_dir,
            quarantine_journal_dir=args.quarantine_journal_dir,
            min_decision_count=args.quarantine_surface_min_decision_count,
            min_promote_count=args.quarantine_surface_min_promote_count,
            min_mean_markout_cents=args.quarantine_surface_min_mean_markout_cents,
            min_win_rate=args.quarantine_surface_min_win_rate,
            min_maker_quote_count=args.quarantine_surface_min_maker_quote_count,
            min_maker_mean_markout_cents=args.quarantine_surface_min_maker_mean_markout_cents,
            generated_at=generated_at,
        )
    if targeted_shadow_enabled:
        if not bool(args.no_targeted_shadow_cooldown):
            targeted_shadow_selection_validation = build_targeted_shadow_validation_report(
                journal_dir=args.targeted_shadow_journal_dir,
                min_marked_count=args.quarantine_min_unlock_count,
                min_mean_markout_cents=args.quarantine_min_unlock_mean_markout_cents,
                min_win_rate=args.quarantine_min_unlock_win_rate,
                min_maker_inferred_fills=args.quarantine_min_maker_inferred_fills,
                generated_at=generated_at,
            )
            targeted_shadow_cooldown = build_targeted_shadow_cooldown_report(
                targeted_shadow_selection_validation,
                min_marked_count=args.targeted_shadow_cooldown_min_marked_count,
                min_mean_markout_cents=args.targeted_shadow_cooldown_min_mean_markout_cents,
                max_win_rate=args.targeted_shadow_cooldown_max_win_rate,
                generated_at=generated_at,
            )
        targeted_shadow_report = build_targeted_shadow_signal_report(
            signal_report,
            quarantine_reasons=args.targeted_shadow_quarantine_reasons or ("risk_rule_only_reject",),
            bucket_types=args.targeted_shadow_bucket_types or ("le",),
            near_miss_categories=args.targeted_shadow_near_miss_categories or ("price", "depth"),
            cooldown_reasons=(targeted_shadow_cooldown or {}).get("cooldown_reasons") or (),
            positive_edge_only=not bool(args.targeted_shadow_include_negative_edge),
            min_edge_percent=args.targeted_shadow_min_edge_percent,
            max_items=args.targeted_shadow_max_fills,
            generated_at=generated_at,
        )
        targeted_shadow_journal = write_paper_journal(
            targeted_shadow_report,
            journal_dir=args.targeted_shadow_journal_dir,
            profile=f"{args.paper_journal_profile}-targeted-shadow",
            include_candidates=False,
            include_watch=False,
            include_quarantine=True,
            max_fills=args.targeted_shadow_max_fills,
            recorded_at=generated_at,
        )
        targeted_shadow_markout = markout_open_paper_fills(
            journal_dir=args.targeted_shadow_journal_dir,
            max_fills=args.targeted_shadow_markout_max_fills,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        targeted_shadow_resolved_audit = audit_paper_fills_resolution(
            journal_dir=args.targeted_shadow_journal_dir,
            backfill_dir=args.backfill_dir,
            max_fills=args.targeted_shadow_audit_max_fills,
            include_unresolved=True,
            recorded_at=generated_at,
        )
        if bool(args.write_maker_quotes):
            targeted_shadow_maker_quote_journal = write_maker_quote_journal_from_fills(
                journal_dir=args.targeted_shadow_journal_dir,
                quote_size=float(args.maker_quote_size),
                quote_offset_cents=args.maker_quote_offset_cents,
                max_quotes=args.maker_max_quotes,
                recorded_at=generated_at,
            )
            targeted_shadow_maker_quote_markout = markout_open_maker_quotes(
                journal_dir=args.targeted_shadow_journal_dir,
                max_quotes=args.maker_markout_max_quotes,
                min_markout_interval_seconds=float(args.markout_min_interval_seconds),
                recorded_at=generated_at,
            )
            targeted_shadow_maker_quote_summary = summarize_maker_quote_journal(args.targeted_shadow_journal_dir)
        targeted_shadow_validation = build_targeted_shadow_validation_report(
            journal_dir=args.targeted_shadow_journal_dir,
            min_marked_count=args.quarantine_min_unlock_count,
            min_mean_markout_cents=args.quarantine_min_unlock_mean_markout_cents,
            min_win_rate=args.quarantine_min_unlock_win_rate,
            min_maker_inferred_fills=args.quarantine_min_maker_inferred_fills,
            generated_at=generated_at,
        )
    if current_signal_taker_enabled:
        current_signal_taker_report = build_current_signal_taker_probe_report(
            signal_report,
            max_items=max(1, int(args.current_signal_taker_max_fills)),
            min_edge_percent=float(args.current_signal_taker_min_edge_percent),
            max_spread=float(args.current_signal_taker_max_spread),
            allowed_bucket_types=current_signal_taker_allowed_bucket_types or None,
            generated_at=generated_at,
        )
        current_signal_taker_journal = write_paper_journal(
            current_signal_taker_report,
            journal_dir=args.current_signal_taker_journal_dir,
            profile=f"{args.paper_journal_profile}-current-signal-taker",
            include_candidates=False,
            include_watch=False,
            include_quarantine=True,
            max_fills=args.current_signal_taker_max_fills,
            recorded_at=generated_at,
            min_reentry_seconds=max(0, int(args.current_signal_taker_min_reentry_seconds)),
        )
        current_signal_taker_markout = markout_open_paper_fills(
            journal_dir=args.current_signal_taker_journal_dir,
            max_fills=args.current_signal_taker_markout_max_fills,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        current_signal_taker_resolved_audit = audit_paper_fills_resolution(
            journal_dir=args.current_signal_taker_journal_dir,
            backfill_dir=args.backfill_dir,
            max_fills=args.current_signal_taker_audit_max_fills,
            include_unresolved=True,
            recorded_at=generated_at,
        )
        current_signal_taker_validation = build_current_signal_taker_validation_report(
            journal_dir=args.current_signal_taker_journal_dir,
            min_marked_count=max(1, int(args.current_signal_taker_validation_min_marked_count)),
            min_mean_markout_cents=float(args.current_signal_taker_validation_min_mean_markout_cents),
            min_win_rate=float(args.current_signal_taker_validation_min_win_rate),
            required_horizons=args.current_signal_taker_validation_required_horizons
            or ("0-5m", "5-15m", "15-30m"),
            min_horizon_count=max(1, int(args.current_signal_taker_validation_min_horizon_count)),
            min_resolved_count=max(0, int(args.current_signal_taker_validation_min_resolved_count)),
            generated_at=generated_at,
        )
    if eq_shadow_enabled:
        if not bool(args.no_eq_shadow_cooldown):
            eq_shadow_selection_validation = build_targeted_shadow_validation_report(
                journal_dir=args.eq_shadow_journal_dir,
                min_marked_count=args.quarantine_min_unlock_count,
                min_mean_markout_cents=args.quarantine_min_unlock_mean_markout_cents,
                min_win_rate=args.quarantine_min_unlock_win_rate,
                min_maker_inferred_fills=args.quarantine_min_maker_inferred_fills,
                generated_at=generated_at,
            )
            eq_shadow_cooldown = build_targeted_shadow_cooldown_report(
                eq_shadow_selection_validation,
                min_marked_count=args.targeted_shadow_cooldown_min_marked_count,
                min_mean_markout_cents=args.targeted_shadow_cooldown_min_mean_markout_cents,
                max_win_rate=args.targeted_shadow_cooldown_max_win_rate,
                generated_at=generated_at,
            )
        eq_shadow_report = build_targeted_shadow_signal_report(
            signal_report,
            quarantine_reasons=("single_non_risk_blocker:bucket_type", "risk_rule_only_reject"),
            bucket_types=("eq",),
            near_miss_categories=("bucket_type",),
            cooldown_reasons=(eq_shadow_cooldown or {}).get("cooldown_reasons") or (),
            positive_edge_only=True,
            min_edge_percent=args.eq_shadow_min_edge_percent,
            require_edge_floor_for_direct_reasons=True,
            max_items=args.eq_shadow_max_fills,
            generated_at=generated_at,
        )
        eq_shadow_journal = write_paper_journal(
            eq_shadow_report,
            journal_dir=args.eq_shadow_journal_dir,
            profile=f"{args.paper_journal_profile}-eq-shadow",
            include_candidates=False,
            include_watch=False,
            include_quarantine=True,
            max_fills=args.eq_shadow_max_fills,
            recorded_at=generated_at,
        )
        eq_shadow_markout = markout_open_paper_fills(
            journal_dir=args.eq_shadow_journal_dir,
            max_fills=args.eq_shadow_markout_max_fills,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        eq_shadow_resolved_audit = audit_paper_fills_resolution(
            journal_dir=args.eq_shadow_journal_dir,
            backfill_dir=args.backfill_dir,
            max_fills=args.eq_shadow_audit_max_fills,
            include_unresolved=True,
            recorded_at=generated_at,
        )
        if bool(args.write_maker_quotes):
            eq_shadow_maker_quote_journal = write_maker_quote_journal_from_fills(
                journal_dir=args.eq_shadow_journal_dir,
                quote_size=float(args.maker_quote_size),
                quote_offset_cents=args.maker_quote_offset_cents,
                max_quotes=args.maker_max_quotes,
                recorded_at=generated_at,
            )
            eq_shadow_maker_quote_markout = markout_open_maker_quotes(
                journal_dir=args.eq_shadow_journal_dir,
                max_quotes=args.maker_markout_max_quotes,
                min_markout_interval_seconds=float(args.markout_min_interval_seconds),
                recorded_at=generated_at,
            )
            eq_shadow_maker_quote_summary = summarize_maker_quote_journal(args.eq_shadow_journal_dir)
        eq_shadow_validation = build_targeted_shadow_validation_report(
            journal_dir=args.eq_shadow_journal_dir,
            min_marked_count=args.quarantine_min_unlock_count,
            min_mean_markout_cents=args.quarantine_min_unlock_mean_markout_cents,
            min_win_rate=args.quarantine_min_unlock_win_rate,
            min_maker_inferred_fills=args.quarantine_min_maker_inferred_fills,
            generated_at=generated_at,
        )
    if maker_focus_enabled:
        maker_focus_execution_calibration = summarize_execution_calibration(
            args.paper_journal_dir,
            latest_only=True,
            min_count=1,
            generated_at=generated_at,
        )
        maker_focus_report = build_maker_focus_signal_report(
            signal_report,
            maker_focus_execution_calibration,
            group_fields=args.maker_focus_group_fields or ("entry_spread_bucket",),
            min_group_count=args.maker_focus_min_group_count,
            min_group_mean_markout_cents=args.maker_focus_min_group_mean_markout_cents,
            min_group_win_rate=args.maker_focus_min_group_win_rate,
            min_edge_percent=args.maker_focus_min_edge_percent,
            respect_maker_quote_risk_rules=not bool(args.maker_focus_ignore_maker_quote_risk_rules),
            max_items=args.maker_focus_max_fills,
            generated_at=generated_at,
        )
        maker_focus_journal = write_paper_journal(
            maker_focus_report,
            journal_dir=args.maker_focus_journal_dir,
            profile=f"{args.paper_journal_profile}-maker-focus",
            include_candidates=False,
            include_watch=False,
            include_quarantine=True,
            max_fills=args.maker_focus_max_fills,
            recorded_at=generated_at,
        )
        maker_focus_markout = markout_open_paper_fills(
            journal_dir=args.maker_focus_journal_dir,
            max_fills=args.maker_focus_markout_max_fills,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        maker_focus_resolved_audit = audit_paper_fills_resolution(
            journal_dir=args.maker_focus_journal_dir,
            backfill_dir=args.backfill_dir,
            max_fills=args.maker_focus_audit_max_fills,
            include_unresolved=True,
            recorded_at=generated_at,
        )
    maker_quote_blocker_calibration = build_maker_quote_blocker_calibration_report(
        signal_report,
        journal_dir=args.paper_journal_dir,
        latest_only=True,
        min_count=args.maker_blocker_calibration_min_count,
        min_inferred_fills=args.maker_blocker_calibration_min_inferred_fills,
        min_fill_inference_rate=args.maker_blocker_calibration_min_fill_rate,
        min_mean_maker_markout_cents=args.maker_blocker_calibration_min_mean_maker_markout_cents,
        min_maker_win_rate=args.maker_blocker_calibration_min_maker_win_rate,
        generated_at=generated_at,
    )
    temperature_opportunity = build_temperature_opportunity_report(
        signal_report,
        maker_quote_blocker_calibration_report=maker_quote_blocker_calibration,
        max_horizon_hours=float(args.temperature_opportunity_max_horizon_hours),
        max_items=max(1, int(args.temperature_opportunity_max_items)),
        generated_at=generated_at,
    )
    temperature_execution_experiment = build_temperature_execution_experiment_report(
        temperature_opportunity,
        offset_cents=args.temperature_execution_offset_cents,
        min_expected_markout_cents=float(args.temperature_execution_min_expected_markout_cents),
        min_win_rate=float(args.temperature_execution_min_win_rate),
        max_items=max(1, int(args.temperature_execution_max_items)),
        generated_at=generated_at,
    )
    if temperature_execution_quotes_enabled:
        temperature_execution_quote_journal = write_temperature_execution_quotes(
            temperature_execution_experiment,
            journal_dir=args.temperature_execution_journal_dir,
            quote_size=float(args.temperature_execution_quote_size),
            max_quotes=args.temperature_execution_max_quotes,
            recorded_at=generated_at,
        )
        temperature_execution_quote_markout = markout_open_maker_quotes(
            journal_dir=args.temperature_execution_journal_dir,
            max_quotes=args.temperature_execution_markout_max_quotes,
            min_markout_interval_seconds=float(args.markout_min_interval_seconds),
            recorded_at=generated_at,
        )
        temperature_execution_quote_summary = summarize_maker_quote_journal(args.temperature_execution_journal_dir)
    if temperature_taker_paper_enabled:
        temperature_taker_paper = run_temperature_taker_paper_cycle(
            execution_journal_dir=args.temperature_execution_journal_dir,
            taker_journal_dir=args.temperature_taker_journal_dir,
            max_items=max(1, int(args.temperature_taker_max_items)),
            max_fills=max(1, int(args.temperature_taker_max_fills)),
            markout_max_fills=max(1, int(args.temperature_taker_markout_max_fills)),
            min_reentry_seconds=max(0, int(args.temperature_taker_min_reentry_seconds)),
            min_markout_interval_seconds=float(args.temperature_taker_min_markout_interval_seconds),
            validation_min_marked_count=max(1, int(args.temperature_taker_validation_min_marked_count)),
            validation_min_mean_markout_cents=float(
                args.temperature_taker_validation_min_mean_markout_cents
            ),
            validation_min_win_rate=float(args.temperature_taker_validation_min_win_rate),
            validation_required_horizons=args.temperature_taker_validation_required_horizons
            or ("0-5m", "5-15m", "15-30m"),
            validation_min_horizon_count=max(1, int(args.temperature_taker_validation_min_horizon_count)),
            validation_min_resolved_count=max(0, int(args.temperature_taker_validation_min_resolved_count)),
            generated_at=generated_at,
        )
    markout = markout_open_paper_fills(
        journal_dir=args.paper_journal_dir,
        max_fills=args.markout_max_fills,
        min_markout_interval_seconds=float(args.markout_min_interval_seconds),
        recorded_at=generated_at,
    )
    resolved_audit = audit_paper_fills_resolution(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        max_fills=args.audit_max_fills,
        include_unresolved=True,
        recorded_at=generated_at,
    )
    resolved_gap_report = build_resolved_gap_report(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        generated_at=generated_at,
        settlement_grace_hours=float(args.resolved_gap_settlement_grace_hours),
    )
    threshold_calibration = build_quality_threshold_calibration_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        min_marked_count=args.calibration_min_marked_count,
        min_mean_markout_cents=args.calibration_min_mean_markout_cents,
        min_win_rate=args.calibration_min_win_rate,
        generated_at=generated_at,
    )
    quarantine_promotion = build_quarantine_promotion_report(
        paper_journal_dir=args.paper_journal_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        signal_report=signal_report,
        min_marked_count=args.quarantine_min_unlock_count,
        min_mean_markout_cents=args.quarantine_min_unlock_mean_markout_cents,
        min_win_rate=args.quarantine_min_unlock_win_rate,
        min_maker_markout_count=args.quarantine_min_maker_inferred_fills,
        generated_at=generated_at,
    )
    readiness = build_live_readiness_report(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        signal_report=signal_report,
        quarantine_surface_report=quarantine_surface,
        maker_quote_blocker_calibration_report=maker_quote_blocker_calibration,
        model_coverage_report=model_coverage,
        temperature_opportunity_report=temperature_opportunity,
        temperature_execution_experiment_report=temperature_execution_experiment,
        current_signal_taker_validation_report=current_signal_taker_validation,
        temperature_taker_validation_report=(temperature_taker_paper or {}).get("taker_validation"),
        temperature_taker_journal_dir=args.temperature_taker_journal_dir,
        include_temperature_taker_validation=bool(temperature_taker_paper_enabled),
        live_permission=bool(args.live_permission),
        generated_at=generated_at,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "live_order_path": False,
        "production_profile": production_profile,
        "effective_profile": {
            "polymarket_queries": list(polymarket_queries),
            "backfill_dir": args.backfill_dir,
            "polymarket_row_limit": polymarket_row_limit,
            "max_city_temperature_queries": max_city_temperature_queries,
            "quality_surface_profile": bool(args.quality_surface_profile) or production_profile,
            "expanded_weather_profile": bool(args.expanded_weather_profile),
            "apply_markout_risk_rules": bool(args.apply_markout_risk_rules) or production_profile,
            "risk_rule_min_count": max(1, int(args.risk_rule_min_count)),
            "risk_rule_mode": args.risk_rule_mode,
            "markout_min_interval_seconds": float(args.markout_min_interval_seconds),
            "suppress_saturated_broad_risk_rules": bool(suppress_saturated_broad_risk_rules),
            "suppress_saturated_partition_risk_rules": bool(suppress_saturated_partition_risk_rules),
            "saturated_risk_rule_min_coverage": float(args.saturated_risk_rule_min_coverage),
            "partition_saturated_risk_rule_min_coverage": float(args.partition_saturated_risk_rule_min_coverage),
            "collector_patch_enabled": bool(args.enable_collector_patch),
            "maker_quote_offset_cents": args.maker_quote_offset_cents or [0.0],
            "max_quarantine": max(1, int(args.max_quarantine)),
            "quarantine_journal_enabled": bool(quarantine_enabled),
            "quarantine_journal_dir": args.quarantine_journal_dir,
            "quarantine_near_miss_categories": list(quarantine_near_miss_categories),
            "quarantine_surface_min_decision_count": int(args.quarantine_surface_min_decision_count),
            "quarantine_surface_min_promote_count": int(args.quarantine_surface_min_promote_count),
            "quarantine_surface_min_mean_markout_cents": float(args.quarantine_surface_min_mean_markout_cents),
            "quarantine_surface_min_win_rate": float(args.quarantine_surface_min_win_rate),
            "quarantine_surface_risk_rules_enabled": bool(quarantine_surface_risk_rules_enabled),
            "resolved_gap_settlement_grace_hours": float(args.resolved_gap_settlement_grace_hours),
            "targeted_shadow_enabled": bool(targeted_shadow_enabled),
            "targeted_shadow_journal_dir": args.targeted_shadow_journal_dir,
            "targeted_shadow_quarantine_reasons": args.targeted_shadow_quarantine_reasons or ["risk_rule_only_reject"],
            "targeted_shadow_bucket_types": args.targeted_shadow_bucket_types or ["le"],
            "targeted_shadow_near_miss_categories": args.targeted_shadow_near_miss_categories or ["price", "depth"],
            "targeted_shadow_min_edge_percent": float(args.targeted_shadow_min_edge_percent),
            "targeted_shadow_positive_edge_only": not bool(args.targeted_shadow_include_negative_edge),
            "targeted_shadow_cooldown_enabled": bool(targeted_shadow_enabled) and not bool(args.no_targeted_shadow_cooldown),
            "targeted_shadow_cooldown_min_marked_count": int(args.targeted_shadow_cooldown_min_marked_count),
            "targeted_shadow_cooldown_min_mean_markout_cents": float(args.targeted_shadow_cooldown_min_mean_markout_cents),
            "targeted_shadow_cooldown_max_win_rate": float(args.targeted_shadow_cooldown_max_win_rate),
            "current_signal_taker_enabled": bool(current_signal_taker_enabled),
            "current_signal_taker_journal_dir": args.current_signal_taker_journal_dir,
            "current_signal_taker_max_fills": int(args.current_signal_taker_max_fills),
            "current_signal_taker_min_edge_percent": float(args.current_signal_taker_min_edge_percent),
            "current_signal_taker_max_spread": float(args.current_signal_taker_max_spread),
            "current_signal_taker_allowed_bucket_types": (
                list(current_signal_taker_allowed_bucket_types)
                if current_signal_taker_allowed_bucket_types
                else None
            ),
            "current_signal_taker_min_reentry_seconds": int(args.current_signal_taker_min_reentry_seconds),
            "current_signal_taker_markout_max_fills": int(args.current_signal_taker_markout_max_fills),
            "current_signal_taker_validation_min_marked_count": int(
                args.current_signal_taker_validation_min_marked_count
            ),
            "current_signal_taker_validation_min_mean_markout_cents": float(
                args.current_signal_taker_validation_min_mean_markout_cents
            ),
            "current_signal_taker_validation_min_win_rate": float(
                args.current_signal_taker_validation_min_win_rate
            ),
            "current_signal_taker_validation_required_horizons": (
                args.current_signal_taker_validation_required_horizons or ["0-5m", "5-15m", "15-30m"]
            ),
            "current_signal_taker_validation_min_horizon_count": int(
                args.current_signal_taker_validation_min_horizon_count
            ),
            "current_signal_taker_validation_min_resolved_count": int(
                args.current_signal_taker_validation_min_resolved_count
            ),
            "eq_shadow_enabled": bool(eq_shadow_enabled),
            "eq_shadow_journal_dir": args.eq_shadow_journal_dir,
            "eq_shadow_bucket_types": ["eq"],
            "eq_shadow_near_miss_categories": ["bucket_type"],
            "eq_shadow_min_edge_percent": float(args.eq_shadow_min_edge_percent),
            "eq_shadow_require_edge_floor_for_direct_reasons": True,
            "eq_shadow_cooldown_enabled": bool(eq_shadow_enabled) and not bool(args.no_eq_shadow_cooldown),
            "maker_focus_enabled": bool(maker_focus_enabled),
            "maker_focus_journal_dir": args.maker_focus_journal_dir,
            "maker_focus_group_fields": args.maker_focus_group_fields or ["entry_spread_bucket"],
            "maker_focus_min_group_count": int(args.maker_focus_min_group_count),
            "maker_focus_min_group_mean_markout_cents": float(args.maker_focus_min_group_mean_markout_cents),
            "maker_focus_min_group_win_rate": float(args.maker_focus_min_group_win_rate),
            "maker_focus_min_edge_percent": float(args.maker_focus_min_edge_percent),
            "maker_focus_respect_maker_quote_risk_rules": not bool(args.maker_focus_ignore_maker_quote_risk_rules),
            "maker_blocker_calibration_min_count": int(args.maker_blocker_calibration_min_count),
            "maker_blocker_calibration_min_inferred_fills": int(args.maker_blocker_calibration_min_inferred_fills),
            "maker_blocker_calibration_min_fill_rate": float(args.maker_blocker_calibration_min_fill_rate),
            "maker_blocker_calibration_min_mean_maker_markout_cents": float(
                args.maker_blocker_calibration_min_mean_maker_markout_cents
            ),
            "maker_blocker_calibration_min_maker_win_rate": float(args.maker_blocker_calibration_min_maker_win_rate),
            "model_coverage_max_horizon_days": float(args.model_coverage_max_horizon_days),
            "model_coverage_thresholds": model_coverage.get("thresholds"),
            "temperature_opportunity_max_horizon_hours": float(args.temperature_opportunity_max_horizon_hours),
            "temperature_opportunity_max_items": int(args.temperature_opportunity_max_items),
            "temperature_execution_offset_cents": (
                args.temperature_execution_offset_cents or [0.0, 0.5, 1.0, 2.0, 3.0]
            ),
            "temperature_execution_min_expected_markout_cents": float(
                args.temperature_execution_min_expected_markout_cents
            ),
            "temperature_execution_min_win_rate": float(args.temperature_execution_min_win_rate),
            "temperature_execution_max_items": int(args.temperature_execution_max_items),
            "temperature_execution_quotes_enabled": bool(temperature_execution_quotes_enabled),
            "temperature_execution_journal_dir": args.temperature_execution_journal_dir,
            "temperature_execution_quote_size": float(args.temperature_execution_quote_size),
            "temperature_execution_max_quotes": int(args.temperature_execution_max_quotes),
            "temperature_execution_markout_max_quotes": int(args.temperature_execution_markout_max_quotes),
            "temperature_taker_paper_enabled": bool(temperature_taker_paper_enabled),
            "temperature_taker_journal_dir": args.temperature_taker_journal_dir,
            "temperature_taker_max_items": int(args.temperature_taker_max_items),
            "temperature_taker_max_fills": int(args.temperature_taker_max_fills),
            "temperature_taker_markout_max_fills": int(args.temperature_taker_markout_max_fills),
            "temperature_taker_min_reentry_seconds": int(args.temperature_taker_min_reentry_seconds),
            "temperature_taker_min_markout_interval_seconds": float(
                args.temperature_taker_min_markout_interval_seconds
            ),
            "temperature_taker_validation_min_marked_count": int(
                args.temperature_taker_validation_min_marked_count
            ),
            "temperature_taker_validation_min_mean_markout_cents": float(
                args.temperature_taker_validation_min_mean_markout_cents
            ),
            "temperature_taker_validation_min_win_rate": float(args.temperature_taker_validation_min_win_rate),
            "temperature_taker_validation_required_horizons": (
                args.temperature_taker_validation_required_horizons or ["0-5m", "5-15m", "15-30m"]
            ),
            "temperature_taker_validation_min_horizon_count": int(
                args.temperature_taker_validation_min_horizon_count
            ),
            "temperature_taker_validation_min_resolved_count": int(
                args.temperature_taker_validation_min_resolved_count
            ),
        },
        "signal_summary": signal_report.get("summary"),
        "risk_rules": {
            key: value
            for key, value in risk_rules_payload.items()
            if key != "rules"
        },
        "source_diagnostics": signal_report.get("source_diagnostics"),
        "model_coverage": model_coverage,
        "temperature_opportunity": temperature_opportunity,
        "temperature_execution_experiment": temperature_execution_experiment,
        "temperature_execution_shadow": (
            {
                "journal": temperature_execution_quote_journal,
                "markout": temperature_execution_quote_markout,
                "summary": temperature_execution_quote_summary,
                "paper_only": True,
                "counts_for_live_gate": False,
            }
            if temperature_execution_quotes_enabled
            else None
        ),
        "temperature_taker_paper": temperature_taker_paper if temperature_taker_paper_enabled else None,
        "paper_journal": paper_journal,
        "maker_quote_journal": maker_quote_journal,
        "maker_quote_markout": maker_quote_markout,
        "maker_quote_summary": maker_quote_summary,
        "quarantine_journal": quarantine_journal,
        "quarantine_markout": (
            {
                key: value
                for key, value in quarantine_markout.items()
                if key != "records"
            }
            if quarantine_markout
            else None
        ),
        "quarantine_resolved_audit": (
            {
                key: value
                for key, value in quarantine_resolved_audit.items()
                if key != "records"
            }
            if quarantine_resolved_audit
            else None
        ),
        "quarantine_maker_quote_journal": quarantine_maker_quote_journal,
        "quarantine_maker_quote_markout": quarantine_maker_quote_markout,
        "quarantine_maker_quote_summary": quarantine_maker_quote_summary,
        "quarantine_validation": quarantine_validation,
        "quarantine_surface": (
            {
                key: value
                for key, value in quarantine_surface.items()
                if key != "groups"
            }
            if quarantine_surface
            else None
        ),
        "targeted_shadow": (
            {
                "signal_summary": (targeted_shadow_report or {}).get("summary"),
                "target_config": (targeted_shadow_report or {}).get("target_config"),
                "selection_cooldown": targeted_shadow_cooldown,
                "journal": targeted_shadow_journal,
                "markout": (
                    {
                        key: value
                        for key, value in targeted_shadow_markout.items()
                        if key != "records"
                    }
                    if targeted_shadow_markout
                    else None
                ),
                "resolved_audit": (
                    {
                        key: value
                        for key, value in targeted_shadow_resolved_audit.items()
                        if key != "records"
                    }
                    if targeted_shadow_resolved_audit
                    else None
                ),
                "maker_quote_journal": targeted_shadow_maker_quote_journal,
                "maker_quote_markout": targeted_shadow_maker_quote_markout,
                "maker_quote_summary": targeted_shadow_maker_quote_summary,
                "validation": targeted_shadow_validation,
                "counts_for_live_gate": False,
                "paper_only": True,
            }
            if targeted_shadow_enabled
            else None
        ),
        "current_signal_taker": (
            {
                "signal_summary": (current_signal_taker_report or {}).get("summary"),
                "config": (current_signal_taker_report or {}).get("config"),
                "hard_conclusion": (current_signal_taker_report or {}).get("hard_conclusion"),
                "journal": current_signal_taker_journal,
                "markout": (
                    {
                        key: value
                        for key, value in current_signal_taker_markout.items()
                        if key != "records"
                    }
                    if current_signal_taker_markout
                    else None
                ),
                "resolved_audit": (
                    {
                        key: value
                        for key, value in current_signal_taker_resolved_audit.items()
                        if key != "records"
                    }
                    if current_signal_taker_resolved_audit
                    else None
                ),
                "validation": current_signal_taker_validation,
                "counts_for_live_gate": False,
                "paper_only": True,
            }
            if current_signal_taker_enabled
            else None
        ),
        "eq_shadow": (
            {
                "signal_summary": (eq_shadow_report or {}).get("summary"),
                "target_config": (eq_shadow_report or {}).get("target_config"),
                "selection_cooldown": eq_shadow_cooldown,
                "journal": eq_shadow_journal,
                "markout": (
                    {
                        key: value
                        for key, value in eq_shadow_markout.items()
                        if key != "records"
                    }
                    if eq_shadow_markout
                    else None
                ),
                "resolved_audit": (
                    {
                        key: value
                        for key, value in eq_shadow_resolved_audit.items()
                        if key != "records"
                    }
                    if eq_shadow_resolved_audit
                    else None
                ),
                "maker_quote_journal": eq_shadow_maker_quote_journal,
                "maker_quote_markout": eq_shadow_maker_quote_markout,
                "maker_quote_summary": eq_shadow_maker_quote_summary,
                "validation": eq_shadow_validation,
                "counts_for_live_gate": False,
                "paper_only": True,
            }
            if eq_shadow_enabled
            else None
        ),
        "maker_focus": (
            {
                "signal_summary": (maker_focus_report or {}).get("summary"),
                "selection_config": (maker_focus_report or {}).get("selection_config"),
                "eligible_group_count": (maker_focus_report or {}).get("eligible_group_count"),
                "eligible_groups": (maker_focus_report or {}).get("eligible_groups"),
                "hard_conclusion": (maker_focus_report or {}).get("hard_conclusion"),
                "execution_calibration": {
                    "fill_count": (maker_focus_execution_calibration or {}).get("fill_count"),
                    "execution_delta": (maker_focus_execution_calibration or {}).get("execution_delta"),
                    "by_execution_and_spread": (maker_focus_execution_calibration or {}).get("by_execution_and_spread"),
                },
                "journal": maker_focus_journal,
                "markout": (
                    {
                        key: value
                        for key, value in maker_focus_markout.items()
                        if key != "records"
                    }
                    if maker_focus_markout
                    else None
                ),
                "resolved_audit": (
                    {
                        key: value
                        for key, value in maker_focus_resolved_audit.items()
                        if key != "records"
                    }
                    if maker_focus_resolved_audit
                    else None
                ),
                "counts_for_live_gate": False,
                "paper_only": True,
            }
            if maker_focus_enabled
            else None
        ),
        "maker_quote_blocker_calibration": (
            {
                **{
                    key: value
                    for key, value in maker_quote_blocker_calibration.items()
                    if key != "blockers"
                },
                "top_blockers": (maker_quote_blocker_calibration.get("blockers") or [])[:10],
            }
            if maker_quote_blocker_calibration
            else None
        ),
        "markout": {
            key: value
            for key, value in markout.items()
            if key != "records"
        },
        "resolved_audit": {
            key: value
            for key, value in resolved_audit.items()
            if key != "records"
        },
        "resolved_gap_report": {
            key: value
            for key, value in resolved_gap_report.items()
            if key != "rows"
        },
        "quality_threshold_calibration": {
            key: value
            for key, value in threshold_calibration.items()
            if key != "top_profiles"
        },
        "quarantine_promotion": {
            key: value
            for key, value in quarantine_promotion.items()
            if key != "groups"
        },
        "live_readiness_progress": readiness,
        "signal_report": signal_report,
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    print(json.dumps(build_cycle(args), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
