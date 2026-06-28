#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_alpha_viability_scoreboard import (  # noqa: E402
    build_alpha_viability_scoreboard,
    load_json,
    update_profit_strategy_state_report,
    write_json,
)


DEFAULT_SCOREBOARD = Path("evidence/alpha_viability_scoreboard.json")
DEFAULT_STRICT = Path("evidence/strict_gate_replay_metar_ltac_uuww_queue_fix_summary.json")
DEFAULT_ARCHIVED = Path("evidence/archived_overlap_replay_metar_ltac_uuww_queue_fix_summary.json")
DEFAULT_TRADABILITY = Path("evidence/historical_replay/observation_lock_tradability_report.json")
DEFAULT_OBS_LOCK_TRADE = Path("evidence/historical_replay/observation_lock_trade_replay_report.json")
DEFAULT_EQ_DEAD_NO_TRADE = Path("evidence/eq_dead_no/eq_dead_no_trade_replay_report.json")
DEFAULT_EQ_DEAD_NO_ROBUSTNESS = Path("evidence/eq_dead_no/eq_dead_no_proxy_robustness_report.json")
DEFAULT_EQ_DEAD_NO_EXPANDED_ROBUSTNESS = Path("evidence/eq_dead_no/eq_dead_no_expanded_proxy_robustness_report.json")
DEFAULT_EQ_DEAD_NO_SAMPLER = Path("evidence/eq_dead_no/eq_dead_no_execution_sampler_report.json")
DEFAULT_EQ_DEAD_NO_MARKOUT = Path("evidence/eq_dead_no/markout_report.json")
DEFAULT_EQ_DEAD_NO_AUDIT = Path("evidence/eq_dead_no/resolved_audit_report.json")
DEFAULT_THRESHOLD_TRADE = Path("evidence/historical_replay/threshold_latency_trade_replay_report.json")
DEFAULT_ACTIVE_SAMPLER = Path("evidence/threshold_latency/threshold_latency_execution_sampler_report.json")
DEFAULT_DUE = Path("evidence/non_dust_uuww_due_verdict.json")
DEFAULT_BUCKET_FAMILY_ARBITRAGE = Path("evidence/bucket_family/basket_arbitrage_report.json")
DEFAULT_BUCKET_FAMILY_LP_ARBITRAGE = Path("evidence/bucket_family/lp_arbitrage_report.json")
DEFAULT_BUCKET_FAMILY_HISTORICAL = Path("evidence/bucket_family/basket_historical_replay_report.json")
DEFAULT_PROFIT_STATE = Path("evidence/profit_strategy_state_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only weather alpha viability scoreboard.")
    parser.add_argument("--strict-replay", default=str(DEFAULT_STRICT))
    parser.add_argument("--archived-overlap-replay", default=str(DEFAULT_ARCHIVED))
    parser.add_argument("--observation-lock-tradability", default=str(DEFAULT_TRADABILITY))
    parser.add_argument("--observation-lock-trade-replay", default=str(DEFAULT_OBS_LOCK_TRADE))
    parser.add_argument("--eq-dead-no-trade-replay", default=str(DEFAULT_EQ_DEAD_NO_TRADE))
    parser.add_argument("--eq-dead-no-proxy-robustness", default=str(DEFAULT_EQ_DEAD_NO_ROBUSTNESS))
    parser.add_argument("--eq-dead-no-expanded-robustness", default=str(DEFAULT_EQ_DEAD_NO_EXPANDED_ROBUSTNESS))
    parser.add_argument("--eq-dead-no-sampler-report", default=str(DEFAULT_EQ_DEAD_NO_SAMPLER))
    parser.add_argument("--eq-dead-no-markout-report", default=str(DEFAULT_EQ_DEAD_NO_MARKOUT))
    parser.add_argument("--eq-dead-no-resolved-audit-report", default=str(DEFAULT_EQ_DEAD_NO_AUDIT))
    parser.add_argument("--threshold-latency-trade-replay", default=str(DEFAULT_THRESHOLD_TRADE))
    parser.add_argument("--active-sampler-report", default=str(DEFAULT_ACTIVE_SAMPLER))
    parser.add_argument("--non-dust-due-status", default=str(DEFAULT_DUE))
    parser.add_argument("--bucket-family-arbitrage-report", default=str(DEFAULT_BUCKET_FAMILY_ARBITRAGE))
    parser.add_argument("--bucket-family-lp-arbitrage-report", default=str(DEFAULT_BUCKET_FAMILY_LP_ARBITRAGE))
    parser.add_argument("--bucket-family-historical-replay-report", default=str(DEFAULT_BUCKET_FAMILY_HISTORICAL))
    parser.add_argument("--summary-output", default=str(DEFAULT_SCOREBOARD))
    parser.add_argument("--profit-strategy-state-output", default=str(DEFAULT_PROFIT_STATE))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    scoreboard = build_alpha_viability_scoreboard(
        strict_replay_report=load_json(args.strict_replay),
        archived_overlap_replay_report=load_json(args.archived_overlap_replay),
        observation_lock_tradability_report=load_json(args.observation_lock_tradability),
        observation_lock_trade_replay_report=load_json(args.observation_lock_trade_replay),
        eq_dead_no_trade_replay_report=load_json(args.eq_dead_no_trade_replay),
        eq_dead_no_proxy_robustness_report=load_json(args.eq_dead_no_proxy_robustness),
        eq_dead_no_expanded_robustness_report=load_json(args.eq_dead_no_expanded_robustness),
        eq_dead_no_sampler_report=load_json(args.eq_dead_no_sampler_report),
        eq_dead_no_markout_report=load_json(args.eq_dead_no_markout_report),
        eq_dead_no_resolved_audit_report=load_json(args.eq_dead_no_resolved_audit_report),
        threshold_latency_trade_replay_report=load_json(args.threshold_latency_trade_replay),
        active_sampler_report=load_json(args.active_sampler_report),
        non_dust_due_runner_status=load_json(args.non_dust_due_status),
        bucket_family_arbitrage_report=load_json(args.bucket_family_arbitrage_report),
        bucket_family_lp_arbitrage_report=load_json(args.bucket_family_lp_arbitrage_report),
        bucket_family_historical_replay_report=load_json(args.bucket_family_historical_replay_report),
        generated_at=args.generated_at,
    )
    write_json(args.summary_output, scoreboard)
    profit_state = update_profit_strategy_state_report(
        existing_report=load_json(args.profit_strategy_state_output),
        scoreboard=scoreboard,
        generated_at=args.generated_at,
    )
    write_json(args.profit_strategy_state_output, profit_state)
    print(json.dumps(scoreboard["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
