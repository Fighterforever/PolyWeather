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

from src.trading.polymarket_alpha.probability_dataset import write_json  # noqa: E402


def _load(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _mean(report: Dict[str, Any], key: str) -> Any:
    return report.get(key)


def _load_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    discovery = _load(args.discovery_report)
    strategy = _load(args.strategy_report)
    paper = _load(args.paper_cycle_report)
    quote_updates = _load(args.quote_update_report)
    reward_risk = _load(args.reward_risk_report)
    lifecycle = _load(args.lifecycle_audit_report)
    reward_share = _load(args.reward_share_report)
    allocation = _load(args.reward_allocation_audit_report)
    cohort_report = _load(args.measurement_cohort_report)
    dollarization = _load(args.reward_dollarization_report)
    dashboard = _load(args.profitability_dashboard)
    cancellation = _load(args.cancellation_policy_report)
    window = _load(args.reward_window_report)
    holder = _load(args.smart_holder_report)
    reward_cents = reward_risk.get("estimated_reward_cents_proxy", paper.get("estimated_reward_cents"))
    reward_cents_proxy = reward_risk.get("estimated_reward_cents_proxy", paper.get("estimated_reward_cents_proxy"))
    reward_points_proxy = reward_risk.get("cumulative_reward_points_proxy", quote_updates.get("cumulative_reward_points_proxy", paper.get("reward_points_proxy")))
    pnl_without = reward_risk.get("mean_current_markout", paper.get("net_estimated_pnl_without_reward"))
    pnl_with = reward_risk.get("net_estimated_pnl_with_reward_proxy", paper.get("net_estimated_pnl_with_reward_proxy", paper.get("net_estimated_pnl_with_reward")))
    quote_count = int(paper.get("paper_quote_count") or 0)
    quote_update_count = int(reward_risk.get("quote_update_count") or quote_updates.get("quote_update_count") or paper.get("quote_update_count") or 0)
    reward_to_risk = reward_risk.get("reward_to_risk_proxy")
    valid_by_horizon = reward_risk.get("valid_markout_count_by_horizon") or []
    valid_counts = {str(row.get("horizon")): int(row.get("count") or 0) for row in valid_by_horizon if isinstance(row, dict)}
    not_old_by_horizon = reward_risk.get("cohort_not_old_enough_count_by_horizon") or []
    not_old_counts = {str(row.get("horizon")): int(row.get("count") or 0) for row in not_old_by_horizon if isinstance(row, dict)}
    lifecycle_conclusion = lifecycle.get("conclusion")
    exact_reward_available = bool(dollarization.get("exact_reward_cents_available_count"))
    dollar_scenarios_positive = any(
        float(dollarization.get(key) or 0.0) > 0
        for key in (
            "scenario_total_reward_if_daily_allocation_1",
            "scenario_total_reward_if_daily_allocation_5",
            "scenario_total_reward_if_daily_allocation_10",
            "scenario_total_reward_if_daily_allocation_25",
            "scenario_total_reward_if_daily_allocation_50",
        )
    )
    observed_markout_total = dollarization.get("observed_markout_total_cents")
    if discovery.get("reward_metadata_available_count", 0) == 0:
        recommendation = "reward_metadata_pipeline_broken_or_no_rewards"
    elif int(cohort_report.get("active_cohort_count") or reward_risk.get("active_cohort_count") or 0) == 0:
        recommendation = "create_measurement_cohorts_before_profit_judgment"
    elif lifecycle_conclusion not in (None, "lifecycle_ok", "missing_horizon_updates_after_entry"):
        recommendation = "fix_quote_lifecycle_before_profit_judgment"
    elif sum(valid_counts.get(key, 0) for key in ("5m", "15m", "1h")) == 0 and sum(not_old_counts.get(key, 0) for key in ("5m", "15m", "1h")) > 0:
        recommendation = "waiting_for_cohort_horizon_markout"
    elif quote_update_count >= 100 and sum(valid_counts.get(key, 0) for key in ("5m", "15m", "1h")) == 0:
        recommendation = "markout_pipeline_still_broken"
    elif quote_update_count < 100:
        recommendation = "continue_weather_lp_paper_insufficient_updates"
    elif (
        observed_markout_total is not None
        and float(observed_markout_total) >= 0
        and not exact_reward_available
        and dollar_scenarios_positive
    ):
        recommendation = "continue_weather_lp_paper_exact_reward_unknown_but_scenarios_positive"
    elif not exact_reward_available and dollar_scenarios_positive:
        recommendation = "continue_weather_lp_paper_need_exact_allocation"
    elif observed_markout_total is not None and float(observed_markout_total) < 0 and not dollar_scenarios_positive:
        recommendation = "tighten_or_pause_if_reward_scenarios_insufficient"
    elif (
        reward_risk.get("mean_current_markout") is not None
        and float(reward_risk.get("mean_current_markout")) >= 0
        and reward_share.get("quotes_where_visible_share_exceeds_break_even")
    ):
        recommendation = "continue_weather_lp_paper_visible_share_covers_break_even"
    elif reward_risk.get("mean_current_markout") is not None and float(reward_risk.get("mean_current_markout")) < -1.0 and (reward_risk.get("break_even_share_p90") is None or float(reward_risk.get("break_even_share_p90") or 0) > 0.01):
        recommendation = "reduce_weather_lp_strategy"
    elif reward_to_risk is not None and float(reward_to_risk) < 1.0:
        recommendation = "reduce_weather_lp_or_tighten_cancellation"
    elif pnl_with is not None and pnl_without is not None and pnl_with > 0 and pnl_without > -float(reward_cents_proxy or reward_cents or 0):
        recommendation = "continue_weather_lp_paper"
    elif reward_cents is not None and quote_count >= 50 and (pnl_with or 0) > 0:
        recommendation = "weather_lp_candidate_for_longer_paper_review"
    else:
        recommendation = "continue_weather_lp_paper"
    city_rows = reward_risk.get("by_city") or []
    best_city = None
    worst_city = None
    if city_rows:
        best_city = max(city_rows, key=lambda row: (row.get("mean_current_markout") is not None, row.get("mean_current_markout") or -999)).get("city")
        worst_city = min(city_rows, key=lambda row: (row.get("mean_current_markout") is None, row.get("mean_current_markout") if row.get("mean_current_markout") is not None else 999)).get("city")
    strategy_rows = reward_risk.get("by_strategy_variant") or []
    best_strategy = None
    if strategy_rows:
        best_strategy = max(strategy_rows, key=lambda row: (row.get("mean_current_markout") is not None, row.get("mean_current_markout") or -999)).get("strategy_variant")
    update_rows = _load_rows(args.quote_updates)
    times = []
    for row in update_rows:
        value = row.get("update_time") or row.get("generated_at")
        if value:
            times.append(str(value))
    actual_window_minutes = None
    if len(times) >= 2:
        try:
            import datetime as _dt

            parsed = [_dt.datetime.fromisoformat(item.replace("Z", "+00:00")) for item in times]
            actual_window_minutes = round((max(parsed) - min(parsed)).total_seconds() / 60.0, 3)
        except Exception:
            actual_window_minutes = None
    city_confidence = "insufficient_sample"
    if city_rows and all(int(row.get("quote_count") or 0) >= 20 for row in city_rows):
        city_confidence = "directional_hint"
    if city_rows and all(int(row.get("quote_count") or 0) >= 50 for row in city_rows):
        city_confidence = "enough_for_filtering"
    return {
        "schema_version": "polyweather_polymarket_alpha_weather_lp_experiment_controller.v1",
        "experiment_start": min(times) if times else None,
        "actual_window_minutes": actual_window_minutes,
        "run_count": window.get("observations_count", 0),
        "reward_market_count": discovery.get("reward_market_count", 0),
        "reward_metadata_available_count": discovery.get("reward_metadata_available_count", 0),
        "reward_qualified_quote_count": strategy.get("reward_qualified_quote_count", 0),
        "paper_quote_count": quote_count,
        "active_quote_count": paper.get("active_quote_count", quote_count),
        "quote_update_count": quote_update_count,
        "unique_quote_id_count": lifecycle.get("unique_quote_id_count"),
        "updates_per_quote_median": lifecycle.get("updates_per_quote_median"),
        "active_cohort_count": cohort_report.get("active_cohort_count") or reward_risk.get("active_cohort_count"),
        "cohort_created_count": cohort_report.get("cohort_created_count") or cohort_report.get("cohorts_created_count"),
        "lifecycle_audit_conclusion": lifecycle_conclusion,
        "lifecycle_missing_horizon_reason_counts": lifecycle.get("missing_horizon_reason_counts"),
        "cumulative_reward_points_proxy": reward_points_proxy,
        "inferred_fill_count": paper.get("inferred_fill_count", 0),
        "estimated_reward_points": paper.get("estimated_reward_points"),
        "reward_points_proxy": reward_points_proxy,
        "estimated_reward_cents": reward_cents,
        "estimated_reward_cents_proxy": reward_cents_proxy,
        "markout_count": reward_risk.get("update_count", paper.get("markout_count", 0)),
        "mean_markout_5m": _mean(reward_risk, "mean_5m_markout"),
        "mean_markout_15m": _mean(reward_risk, "mean_15m_markout"),
        "mean_markout_1h": _mean(reward_risk, "mean_1h_markout"),
        "mean_markout_6h": _mean(reward_risk, "mean_6h_markout"),
        "mean_markout_24h": _mean(reward_risk, "mean_24h_markout"),
        "mean_markout_current": _mean(reward_risk, "mean_current_markout"),
        "valid_markout_count_by_horizon": reward_risk.get("valid_markout_count_by_horizon"),
        "mean_5m_markout": _mean(reward_risk, "mean_5m_markout"),
        "mean_15m_markout": _mean(reward_risk, "mean_15m_markout"),
        "mean_1h_markout": _mean(reward_risk, "mean_1h_markout"),
        "mean_6h_markout": _mean(reward_risk, "mean_6h_markout"),
        "mean_24h_markout": _mean(reward_risk, "mean_24h_markout"),
        "mean_current_markout": _mean(reward_risk, "mean_current_markout"),
        "reward_to_risk_proxy": reward_to_risk,
        "exact_reward_conversion_available": bool(reward_risk.get("exact_reward_conversion_available_count")),
        "break_even_share_median": reward_risk.get("break_even_share_median"),
        "break_even_share_p90": reward_risk.get("break_even_share_p90"),
        "visible_reward_share_median": reward_share.get("visible_reward_share_median"),
        "visible_reward_share_p10": reward_share.get("visible_reward_share_p10"),
        "visible_reward_share_p90": reward_share.get("visible_reward_share_p90"),
        "visible_share_exceeds_break_even_count": reward_share.get("quotes_where_visible_share_exceeds_break_even"),
        "allocation_exact_available": bool(allocation.get("estimated_reward_cents_available_count")),
        "exact_reward_available": exact_reward_available,
        "allocation_available_count": reward_share.get("allocation_available_count") or allocation.get("reward_allocation_available_count"),
        "visible_proxy_reward_available": bool(reward_share.get("visible_proxy_reward_cents_count")),
        "break_even_share_current_median": reward_share.get("median_break_even_share_current") or reward_risk.get("break_even_share_median"),
        "break_even_share_minus_1c_median": reward_share.get("median_break_even_share_under_minus_1c"),
        "break_even_share_minus_3c_median": reward_share.get("median_break_even_share_under_minus_3c"),
        "allocation_audit_gap_counts": allocation.get("gap_counts"),
        "scenario_reward_0_5pct_share": dollarization.get("scenario_total_reward_if_0_5pct_share") or reward_risk.get("scenario_reward_0_5pct_share"),
        "scenario_reward_1pct_share": dollarization.get("scenario_total_reward_if_1pct_share") or reward_risk.get("scenario_reward_1pct_share"),
        "scenario_reward_2pct_share": dollarization.get("scenario_total_reward_if_2pct_share"),
        "scenario_reward_5pct_share": dollarization.get("scenario_total_reward_if_5pct_share"),
        "scenario_reward_visible_median_share": dollarization.get("scenario_total_reward_if_daily_allocation_10"),
        "scenario_reward_daily_allocation_1": dollarization.get("scenario_total_reward_if_daily_allocation_1"),
        "scenario_reward_daily_allocation_5": dollarization.get("scenario_total_reward_if_daily_allocation_5"),
        "scenario_reward_daily_allocation_10": dollarization.get("scenario_total_reward_if_daily_allocation_10"),
        "scenario_reward_daily_allocation_25": dollarization.get("scenario_total_reward_if_daily_allocation_25"),
        "scenario_reward_daily_allocation_50": dollarization.get("scenario_total_reward_if_daily_allocation_50"),
        "observed_markout_total_cents": dollarization.get("observed_markout_total_cents"),
        "break_even_daily_allocation_for_minus_3c": dollarization.get("break_even_daily_allocation_median"),
        "profitability_dashboard_path": str(args.profitability_dashboard),
        "share_scenario_reward_0_5pct_share": reward_share.get("scenario_reward_0_5pct_share"),
        "share_scenario_reward_1pct_share": reward_share.get("scenario_reward_1pct_share"),
        "adverse_selection_count": reward_risk.get("adverse_selection_count", paper.get("adverse_selection_count", 0)),
        "net_estimated_pnl_with_reward": pnl_with,
        "net_estimated_pnl_with_reward_proxy": pnl_with,
        "net_estimated_pnl_without_reward": pnl_without,
        "by_city": reward_risk.get("by_city") or [],
        "by_strategy_variant": reward_risk.get("by_strategy_variant") or [],
        "tentative_best_city": best_city,
        "tentative_worst_city": worst_city,
        "best_city": best_city if city_confidence != "insufficient_sample" else None,
        "worst_city": worst_city if city_confidence != "insufficient_sample" else None,
        "city_confidence": city_confidence,
        "best_strategy_variant": best_strategy,
        "cancellation_policy_recommendation": cancellation.get("cancellation_policy_recommendation"),
        "quote_optimizer_selected_count": _load(args.quote_optimizer_report).get("selected_quote_count"),
        "rejected_expensive_basket_count": _load(args.quote_optimizer_report).get("rejected_expensive_basket_count"),
        "expensive_basket_rejection_count": strategy.get("expensive_basket_rejection_count", 0),
        "smart_holder_signal_count": holder.get("smart_holder_signal_count", 0),
        "time_window_confidence": window.get("window_confidence") or window.get("confidence"),
        "profitability_dashboard_recommendation": dashboard.get("recommendation"),
        "next_expected_5m_markout_time": cohort_report.get("next_expected_5m_markout_time") or reward_risk.get("next_expected_5m_markout_time"),
        "next_expected_15m_markout_time": cohort_report.get("next_expected_15m_markout_time") or reward_risk.get("next_expected_15m_markout_time"),
        "next_expected_1h_markout_time": cohort_report.get("next_expected_1h_markout_time") or reward_risk.get("next_expected_1h_markout_time"),
        "recommendation": recommendation,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-report", default="evidence/weather_lp_rewards/lp_reward_discovery_report.json")
    parser.add_argument("--strategy-report", default="evidence/weather_lp_rewards/weather_lp_strategy_report.json")
    parser.add_argument("--paper-cycle-report", default="evidence/weather_lp_rewards/paper_cycle_report.json")
    parser.add_argument("--quote-update-report", default="evidence/weather_lp_rewards/paper_quote_update_report.json")
    parser.add_argument("--reward-risk-report", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--lifecycle-audit-report", default="evidence/weather_lp_rewards/quote_lifecycle_audit_report.json")
    parser.add_argument("--measurement-cohort-report", default="evidence/weather_lp_rewards/measurement_cohort_report.json")
    parser.add_argument("--reward-share-report", default="evidence/weather_lp_rewards/reward_share_estimator_report.json")
    parser.add_argument("--reward-allocation-audit-report", default="evidence/weather_lp_rewards/reward_allocation_audit_report.json")
    parser.add_argument("--reward-dollarization-report", default="evidence/weather_lp_rewards/reward_dollarization_report.json")
    parser.add_argument("--profitability-dashboard", default="evidence/weather_lp_rewards/weather_lp_profitability_dashboard.json")
    parser.add_argument("--cancellation-policy-report", default="evidence/weather_lp_rewards/cancellation_policy_report.json")
    parser.add_argument("--quote-optimizer-report", default="evidence/weather_lp_rewards/quote_optimizer_report.json")
    parser.add_argument("--quote-updates", default="evidence/weather_lp_rewards/paper_quote_updates.jsonl")
    parser.add_argument("--reward-window-report", default="evidence/weather_lp_rewards/reward_window_report.json")
    parser.add_argument("--smart-holder-report", default="evidence/weather_lp_rewards/smart_holder_signal_report.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_report(args)
    write_json(args.summary_output, report)
    print(json.dumps({"paper_quote_count": report.get("paper_quote_count"), "recommendation": report.get("recommendation"), "live_order_path": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
