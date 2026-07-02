from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_manual_payout_audit_plan import build_manual_payout_audit_plan, render_markdown


def test_manual_payout_audit_plan_is_manual_only_and_limited():
    report = build_manual_payout_audit_plan(
        selected_quotes_report={"selected_quote_count": 3, "total_selected_capital_at_risk": 21.0},
        manual_kill_switch_checklist={"markout_loss_cancel_threshold": "-3c"},
        reward_payout_audit_report={"audit_status": "manual_audit_required"},
        recommended_first_test_capital=30.0,
        max_total_capital_at_risk=90.0,
    )

    assert report["plan_status"] == "ready_for_user_manual_audit"
    assert report["explicit_constraints"]["manual_ui_only"] is True
    assert report["explicit_constraints"]["no_api_order_instructions"] is True
    assert report["maximum_capital"]["max_total_capital_at_risk"] == 70.0
    assert report["maximum_capital"]["recommended_first_test_capital"] <= 25.0
    assert report["max_number_of_manual_quotes"]["first_test_quote_count"] == 3
    assert report["live_order_path"] is False
    rendered = render_markdown(report)
    assert "manual UI only" in rendered
    assert "live_order_path=false" in rendered


def test_manual_payout_audit_plan_blocks_without_selected_quotes():
    report = build_manual_payout_audit_plan(
        selected_quotes_report={"selected_quote_count": 0, "total_selected_capital_at_risk": 0},
        manual_kill_switch_checklist={},
        reward_payout_audit_report={},
    )

    assert report["plan_status"] == "blocked_no_selected_quotes"
    assert report["live_order_path"] is False
