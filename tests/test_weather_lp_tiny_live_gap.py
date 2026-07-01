from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_tiny_live_gap import build_weather_lp_tiny_live_gap_report


def test_tiny_live_gap_never_enables_live_and_lists_gaps():
    report = build_weather_lp_tiny_live_gap_report(
        profitability_simulation_report={"scenario_status": {"base_positive": True}, "observed_markout_total_cents": 10.0},
        weather_lp_experiment_report={"quote_update_count": 120},
        reward_risk_report={"valid_markout_count_by_horizon": [{"horizon": "5m", "count": 1}]},
        position_sizing_report={"quote_size_limit_ready": True, "max_total_capital_at_risk_ready": True},
        kill_switch_policy_report={"daily_stop_loss_ready": True, "manual_kill_switch_ready": False},
    )

    assert report["current_status"] == "paper_only"
    assert report["live_ready"] is False
    assert "manual_kill_switch" in report["missing_controls"]
    assert "need_500_plus_quote_updates" in report["tiny_live_not_allowed_reason"]
    assert report["live_order_path"] is False


def test_tiny_live_gap_accepts_base_positive_but_still_blocks_live():
    report = build_weather_lp_tiny_live_gap_report(
        profitability_simulation_report={"exact_reward_available": True, "scenario_status": {"base_positive": True}},
        weather_lp_experiment_report={"quote_update_count": 600},
        reward_risk_report={
            "valid_markout_count_by_horizon": [
                {"horizon": "5m", "count": 20},
                {"horizon": "15m", "count": 20},
                {"horizon": "1h", "count": 20},
                {"horizon": "6h", "count": 20},
            ]
        },
        position_sizing_report={"quote_size_limit_ready": True, "max_total_capital_at_risk_ready": True, "city_exposure_cap_ready": True},
        kill_switch_policy_report={"daily_stop_loss_ready": True, "manual_kill_switch_ready": True, "cancellation_policy_ready": True},
    )

    assert "manual_order_sheet_not_ready" in report["tiny_live_not_allowed_reason"]
    assert report["final_status"] == "paper_only_needs_more_evidence"
    assert report["live_order_path"] is False


def test_tiny_live_gap_uses_manual_kill_switch_checklist_ready():
    report = build_weather_lp_tiny_live_gap_report(
        profitability_simulation_report={"scenario_status": {"base_positive": True}},
        weather_lp_experiment_report={"quote_update_count": 600},
        reward_risk_report={
            "valid_markout_count_by_horizon": [
                {"horizon": "5m", "count": 20},
                {"horizon": "15m", "count": 20},
                {"horizon": "1h", "count": 20},
                {"horizon": "6h", "count": 20},
            ]
        },
        position_sizing_report={"quote_size_limit_ready": True, "max_total_capital_at_risk_ready": True, "city_exposure_cap_ready": True},
        kill_switch_policy_report={"daily_stop_loss_ready": True, "manual_kill_switch_ready": False, "cancellation_policy_ready": True},
        manual_kill_switch_checklist={"checklist_status": "ready", "ready": True},
        manual_order_sheet_report={"suggested_manual_quote_count": 1},
        impact_simulator_report={"impact_simulation_ready": True},
        reward_payout_audit_report={"audit_status": "manual_audit_required"},
    )

    assert "manual_kill_switch" not in report["missing_controls"]
    assert "manual_kill_switch_not_ready" not in report["remaining_blockers"]
    assert report["kill_switch_ready"] is True


def test_tiny_live_gap_marks_manual_payout_audit_plan_ready():
    report = build_weather_lp_tiny_live_gap_report(
        profitability_simulation_report={"scenario_status": {"base_positive": True}},
        weather_lp_experiment_report={"quote_update_count": 600},
        reward_risk_report={
            "valid_markout_count_by_horizon": [
                {"horizon": "5m", "count": 20},
                {"horizon": "15m", "count": 20},
                {"horizon": "1h", "count": 20},
                {"horizon": "6h", "count": 20},
            ]
        },
        position_sizing_report={"quote_size_limit_ready": True, "max_total_capital_at_risk_ready": True, "city_exposure_cap_ready": True},
        kill_switch_policy_report={"daily_stop_loss_ready": True, "cancellation_policy_ready": True},
        manual_kill_switch_checklist={"checklist_status": "ready", "ready": True},
        manual_order_sheet_report={"suggested_manual_quote_count": 11},
        impact_simulator_report={"impact_simulation_ready": True},
        reward_payout_audit_report={"audit_status": "manual_audit_required"},
        manual_payout_audit_plan_report={"plan_status": "ready_for_user_manual_audit"},
        selected_audit_quotes_report={"selected_quote_count": 3, "total_selected_capital_at_risk": 21},
        manual_payout_audit_result={"audit_status": "waiting_for_manual_audit"},
    )

    assert report["tiny_live_gap_status"] == "manual_payout_audit_plan_ready_but_not_executed"
    assert report["selected_manual_audit_quote_count"] == 3
    assert "actual_reward_payout_not_verified" in report["remaining_blockers"]
    assert report["recommendation"] == "prepare_manual_payout_audit__do_not_enable_auto_live__wait_for_user_manual_audit_result"
    assert report["live_order_path"] is False
