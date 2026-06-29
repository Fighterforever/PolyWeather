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
    if discovery.get("reward_metadata_available_count", 0) == 0:
        recommendation = "reward_metadata_pipeline_broken_or_no_rewards"
    elif quote_update_count < 100:
        recommendation = "continue_weather_lp_paper_insufficient_updates"
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
        "mean_markout_current": _mean(reward_risk, "mean_current_markout"),
        "valid_markout_count_by_horizon": reward_risk.get("valid_markout_count_by_horizon"),
        "mean_5m_markout": _mean(reward_risk, "mean_5m_markout"),
        "mean_15m_markout": _mean(reward_risk, "mean_15m_markout"),
        "mean_1h_markout": _mean(reward_risk, "mean_1h_markout"),
        "mean_current_markout": _mean(reward_risk, "mean_current_markout"),
        "reward_to_risk_proxy": reward_to_risk,
        "exact_reward_conversion_available": bool(reward_risk.get("exact_reward_conversion_available_count")),
        "break_even_share_median": reward_risk.get("break_even_share_median"),
        "break_even_share_p90": reward_risk.get("break_even_share_p90"),
        "scenario_reward_0_5pct_share": reward_risk.get("scenario_reward_0_5pct_share"),
        "scenario_reward_1pct_share": reward_risk.get("scenario_reward_1pct_share"),
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
