from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.live_safety import credentials_present, load_live_safety_config
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
    reward_payout_audit_report: Dict[str, Any] | None = None,
    manual_order_sheet_report: Dict[str, Any] | None = None,
    impact_simulator_report: Dict[str, Any] | None = None,
    manual_kill_switch_checklist: Dict[str, Any] | None = None,
    manual_payout_audit_plan_report: Dict[str, Any] | None = None,
    selected_audit_quotes_report: Dict[str, Any] | None = None,
    manual_payout_audit_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    position_sizing_report = position_sizing_report or {}
    kill_switch_policy_report = kill_switch_policy_report or {}
    reward_payout_audit_report = reward_payout_audit_report or {}
    manual_order_sheet_report = manual_order_sheet_report or {}
    impact_simulator_report = impact_simulator_report or {}
    manual_kill_switch_checklist = manual_kill_switch_checklist or {}
    manual_payout_audit_plan_report = manual_payout_audit_plan_report or {}
    selected_audit_quotes_report = selected_audit_quotes_report or {}
    manual_payout_audit_result = manual_payout_audit_result or {}
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
    manual_kill_switch_ready = bool(
        kill_switch_policy_report.get("manual_kill_switch_ready")
        or manual_kill_switch_checklist.get("ready")
        or manual_kill_switch_checklist.get("checklist_status") == "ready"
    )
    controls_ready = {
        "expensive_basket_filter": bool(position_sizing_report.get("expensive_basket_filter_ready", True)),
        "cancellation_policy": bool(kill_switch_policy_report.get("cancellation_policy_ready", True)),
        "city_filter": bool(position_sizing_report.get("city_exposure_cap_ready", True)),
        "quote_size_limit": bool(position_sizing_report.get("quote_size_limit_ready")),
        "max_daily_loss": bool(kill_switch_policy_report.get("daily_stop_loss_ready")),
        "max_capital_at_risk": bool(position_sizing_report.get("max_total_capital_at_risk_ready")),
        "manual_kill_switch": manual_kill_switch_ready,
    }
    missing_controls = [key for key, value in controls_ready.items() if not value]
    paper_quote_count = int(weather_lp_experiment_report.get("paper_quote_count") or profitability_simulation_report.get("quote_count") or 0)
    total_time_on_book_hours = profitability_simulation_report.get("total_time_on_book_hours") or weather_lp_experiment_report.get("total_time_on_book_hours")
    base_net = (profitability_simulation_report.get("scenario_table") or {}).get("base_net") or weather_lp_experiment_report.get("base_scenario_net_cents")
    conservative_net = (profitability_simulation_report.get("scenario_table") or {}).get("conservative_net") or weather_lp_experiment_report.get("conservative_scenario_net_cents")
    reward_payout_status = reward_payout_audit_report.get("audit_status") or "missing"
    manual_order_ready = int(manual_order_sheet_report.get("suggested_manual_quote_count") or 0) > 0
    impact_ready = bool(impact_simulator_report.get("impact_simulation_ready"))
    checklist_status = str(manual_kill_switch_checklist.get("checklist_status") or "")
    kill_switch_ready = checklist_status == "ready"
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
    remaining_blockers: List[str] = list(evidence_missing)
    if bool(scenario_status.get("base_positive")) and not exact_reward_available:
        remaining_blockers.append("exact_reward_payout_not_verified")
    if not manual_order_ready:
        remaining_blockers.append("manual_order_sheet_not_ready")
    if not impact_ready:
        remaining_blockers.append("live_impact_not_simulated")
    if not kill_switch_ready:
        remaining_blockers.append("manual_kill_switch_not_ready")
    if reward_payout_status != "payout_observed":
        remaining_blockers.append("actual_reward_payout_not_observed")
    manual_plan_ready = manual_payout_audit_plan_report.get("plan_status") == "ready_for_user_manual_audit"
    selected_audit_quote_count = int(selected_audit_quotes_report.get("selected_quote_count") or 0)
    manual_audit_result_status = manual_payout_audit_result.get("audit_status")
    if manual_plan_ready and selected_audit_quote_count > 0 and manual_audit_result_status != "manual_audit_ingested":
        remaining_blockers.append("actual_reward_payout_not_verified")
    tiny_live_not_allowed_reason = "paper_only_no_live_review_authorization"
    if remaining_blockers:
        remaining_blockers = list(dict.fromkeys(remaining_blockers))
        tiny_live_not_allowed_reason = ",".join(remaining_blockers)
    final_status = "paper_only_needs_more_evidence"
    if not remaining_blockers and bool(scenario_status.get("base_positive")):
        final_status = "tiny_live_review_artifacts_ready_but_live_disabled"
    elif manual_plan_ready and selected_audit_quote_count > 0 and manual_audit_result_status != "manual_audit_ingested":
        final_status = "manual_payout_audit_plan_ready_but_not_executed"
    elif manual_order_ready and impact_ready and bool(scenario_status.get("base_positive")):
        final_status = "manual_review_artifacts_partial_exact_payout_or_kill_switch_blocked"
    recommendation = "prepare_manual_review_artifacts_but_keep_live_disabled" if manual_order_ready else "continue_weather_lp_paper_and_collect_exact_reward_allocation"
    if final_status == "manual_payout_audit_plan_ready_but_not_executed":
        recommendation = "prepare_manual_payout_audit__do_not_enable_auto_live__wait_for_user_manual_audit_result"
    return {
        "schema_version": SCHEMA_VERSION,
        "current_status": "paper_only",
        "live_ready": False,
        "live_order_path": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "exact_reward_available": exact_reward_available,
        "paper_quote_count": paper_quote_count,
        "quote_update_count": quote_update_count,
        "total_time_on_book_hours": total_time_on_book_hours,
        "valid_markout_count_by_horizon": reward_risk_report.get("valid_markout_count_by_horizon"),
        "observed_markout_total_cents": profitability_simulation_report.get("observed_markout_total_cents"),
        "base_scenario_net_cents": base_net,
        "conservative_scenario_net_cents": conservative_net,
        "reward_payout_audit_status": reward_payout_status,
        "manual_order_sheet_ready": manual_order_ready,
        "impact_simulation_ready": impact_ready,
        "kill_switch_ready": kill_switch_ready,
        "manual_payout_audit_plan_ready": manual_plan_ready,
        "selected_manual_audit_quote_count": selected_audit_quote_count,
        "selected_manual_audit_capital_at_risk": selected_audit_quotes_report.get("total_selected_capital_at_risk"),
        "manual_payout_audit_result_status": manual_audit_result_status or "waiting_for_manual_audit",
        "remaining_blockers": list(dict.fromkeys(remaining_blockers)),
        "minimum_next_evidence_needed": {
            "reward_payout": "observe actual reward payout or fill manual payout audit template",
            "impact": "impact simulator must pass without crossing/taking",
            "kill_switch": "operator must confirm manual kill-switch checklist",
            "paper": "keep base scenario positive while collecting exact reward allocation evidence",
        },
        "final_status": final_status,
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
        "tiny_live_gap_status": final_status,
        "recommendation": recommendation,
        "next_action": recommendation,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def build_weather_lp_tiny_live_readiness_report(
    *,
    weather_lp_experiment_report: Dict[str, Any],
    tiny_live_selected_orders_report: Dict[str, Any],
    tiny_live_runner_report: Dict[str, Any] | None = None,
    tiny_live_systemd_install_report: Dict[str, Any] | None = None,
    env: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    tiny_live_runner_report = tiny_live_runner_report or {}
    tiny_live_systemd_install_report = tiny_live_systemd_install_report or {}
    env = env or dict(os.environ)
    config = load_live_safety_config(env)
    creds = credentials_present(env)
    selected_count = int(tiny_live_selected_orders_report.get("selected_order_count") or 0)
    selected_capital = _safe_float(tiny_live_selected_orders_report.get("selected_total_capital_at_risk")) or 0.0
    base_net = _safe_float(weather_lp_experiment_report.get("base_scenario_net_cents"))
    conservative_net = _safe_float(weather_lp_experiment_report.get("conservative_scenario_net_cents"))
    exact_reward_available = bool(weather_lp_experiment_report.get("exact_reward_available"))
    kill_switch_ready = bool(weather_lp_experiment_report.get("kill_switch_ready") or weather_lp_experiment_report.get("manual_kill_switch_ready"))
    live_runner_installed = bool(tiny_live_systemd_install_report.get("service_installed") or tiny_live_runner_report)
    live_runner_enabled = bool(tiny_live_systemd_install_report.get("timer_enabled"))
    blockers: List[str] = []
    if selected_count <= 0:
        blockers.append("selected_orders_required")
    if not kill_switch_ready:
        blockers.append("kill_switch_required")
    if not creds:
        blockers.append("credentials_required")
    if not config["global_live_enabled"] or not config["strategy_live_enabled"]:
        blockers.append("enable_flags_required")
    if live_runner_enabled:
        blockers.append("unexpected_live_timer_enabled")
    recommendation_parts: List[str] = []
    if live_runner_installed and not live_runner_enabled and selected_count > 0 and kill_switch_ready:
        recommendation_parts.append("ready_for_user_manual_enable_tiny_live_audit")
    else:
        recommendation_parts.append("not_ready_for_tiny_live_audit")
    if not creds:
        recommendation_parts.append("credentials_required")
    if not config["global_live_enabled"] or not config["strategy_live_enabled"]:
        recommendation_parts.append("enable_flags_required")
    if not exact_reward_available:
        recommendation_parts.append("exact_reward_still_unverified")
    return {
        "schema_version": f"{SCHEMA_VERSION}.readiness.v1",
        "paper_evidence_summary": {
            "paper_quote_count": weather_lp_experiment_report.get("paper_quote_count"),
            "quote_update_count": weather_lp_experiment_report.get("quote_update_count"),
            "base_scenario_net_cents": base_net,
            "conservative_scenario_net_cents": conservative_net,
            "exact_reward_available": exact_reward_available,
        },
        "selected_order_count": selected_count,
        "selected_total_capital_at_risk": selected_capital,
        "base_scenario_positive": bool(base_net is not None and base_net > 0),
        "conservative_scenario_negative": bool(conservative_net is not None and conservative_net < 0),
        "exact_reward_available": exact_reward_available,
        "kill_switch_ready": kill_switch_ready,
        "live_safety_gates_ready": selected_count > 0 and selected_capital <= float(config["max_total_capital_usd"]),
        "live_runner_installed": live_runner_installed,
        "live_runner_enabled": live_runner_enabled,
        "credentials_present": creds,
        "global_live_enabled": config["global_live_enabled"],
        "strategy_live_enabled": config["strategy_live_enabled"],
        "max_total_capital": config["max_total_capital_usd"],
        "remaining_blockers": list(dict.fromkeys(blockers)),
        "recommendation": "__".join(recommendation_parts),
        "paper_only_reference": True,
        "counts_for_live_gate": False,
        "live_order_path": bool(tiny_live_runner_report.get("live_order_path") is True),
        "live_order_path_default": False,
    }


__all__ = [
    "SCHEMA_VERSION",
    "build_weather_lp_tiny_live_gap_report",
    "build_weather_lp_tiny_live_readiness_report",
    "load_json",
    "write_json",
]
