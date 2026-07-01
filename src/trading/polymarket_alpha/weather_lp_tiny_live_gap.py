from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_tiny_live_gap.v1"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count_horizon(report: Dict[str, Any], horizon: str) -> int:
    for row in report.get("valid_markout_count_by_horizon") or []:
        if isinstance(row, dict) and str(row.get("horizon")) == horizon:
            try:
                return int(row.get("count") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def build_weather_lp_tiny_live_gap_report(
    *,
    profitability_simulation_report: Dict[str, Any],
    weather_lp_experiment_report: Dict[str, Any],
    reward_risk_report: Dict[str, Any],
    position_sizing_report: Dict[str, Any] | None = None,
    kill_switch_policy_report: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    position_sizing_report = position_sizing_report or {}
    kill_switch_policy_report = kill_switch_policy_report or {}
    scenario_status = profitability_simulation_report.get("scenario_status") or {}
    markout_ready = {
        horizon: _count_horizon(reward_risk_report, horizon) > 0
        for horizon in ("5m", "15m", "1h", "6h")
    }
    quote_update_count = int(weather_lp_experiment_report.get("quote_update_count") or reward_risk_report.get("quote_update_count") or 0)
    exact_reward_available = bool(
        profitability_simulation_report.get("exact_reward_available")
        or weather_lp_experiment_report.get("exact_reward_available")
    )
    controls_ready = {
        "expensive_basket_filter": bool(position_sizing_report.get("expensive_basket_filter_ready", True)),
        "cancellation_policy": bool(kill_switch_policy_report.get("cancellation_policy_ready", True)),
        "city_filter": bool(position_sizing_report.get("city_exposure_cap_ready", True)),
        "quote_size_limit": bool(position_sizing_report.get("quote_size_limit_ready")),
        "max_daily_loss": bool(kill_switch_policy_report.get("daily_stop_loss_ready")),
        "max_capital_at_risk": bool(position_sizing_report.get("max_total_capital_at_risk_ready")),
        "manual_kill_switch": bool(kill_switch_policy_report.get("manual_kill_switch_ready")),
    }
    missing_controls = [key for key, value in controls_ready.items() if not value]
    evidence_missing: List[str] = []
    if quote_update_count < 500:
        evidence_missing.append("need_500_plus_quote_updates")
    if not all(markout_ready.values()):
        evidence_missing.append("need_valid_5m_15m_1h_6h_markout")
    if not (scenario_status.get("conservative_positive") or scenario_status.get("base_positive")):
        evidence_missing.append("need_conservative_or_base_scenario_positive")
    if not exact_reward_available:
        evidence_missing.append("need_exact_reward_allocation_or_longer_robust_visible_share_haircut")
    if missing_controls:
        evidence_missing.append("need_all_core_risk_controls_ready")
    tiny_live_not_allowed_reason = "paper_only_no_live_review_authorization"
    if evidence_missing:
        tiny_live_not_allowed_reason = ",".join(evidence_missing)
    return {
        "schema_version": SCHEMA_VERSION,
        "current_status": "paper_only",
        "live_ready": False,
        "live_order_path": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "exact_reward_available": exact_reward_available,
        "quote_update_count": quote_update_count,
        "valid_markout_count_by_horizon": reward_risk_report.get("valid_markout_count_by_horizon"),
        "observed_markout_total_cents": profitability_simulation_report.get("observed_markout_total_cents"),
        "scenario_profitability_status": {
            "conservative_positive": bool(scenario_status.get("conservative_positive")),
            "base_positive": bool(scenario_status.get("base_positive")),
            "optimistic_positive": bool(scenario_status.get("optimistic_positive")),
        },
        "risk_controls_ready": controls_ready,
        "missing_controls": missing_controls,
        "minimum_extra_evidence_needed": {
            "quote_update_count_target": 500,
            "days_of_observation_target": 3,
            "exact_reward_allocation_target": "official_total_market_q_score_or_longer_visible_share_haircut_validation",
            "worst_case_stress_pass_target": "base_or_conservative_positive_after_minus_3c_stress",
        },
        "tiny_live_not_allowed_reason": tiny_live_not_allowed_reason,
        "next_action": "continue_weather_lp_paper_and_collect_exact_reward_allocation" if not exact_reward_available else "continue_weather_lp_paper_until_controls_and_multiday_markout_pass",
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["SCHEMA_VERSION", "build_weather_lp_tiny_live_gap_report", "load_json", "write_json"]
