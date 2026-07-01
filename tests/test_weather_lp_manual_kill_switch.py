from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_manual_kill_switch import (
    build_weather_lp_manual_kill_switch_checklist,
    render_markdown,
)


def test_manual_kill_switch_ready_when_required_fields_exist():
    report = build_weather_lp_manual_kill_switch_checklist(
        position_sizing_report={"recommended_total_capital_at_risk": 50, "per_market_risk_cap_dollars": 10},
        kill_switch_policy_report={"daily_stop_loss_ready": True, "max_daily_loss_cents": 300, "max_open_quotes": 10},
    )

    assert report["checklist_status"] == "ready"
    assert report["manual_operator_required"] is True
    assert report["do_not_auto_trade"] is True
    assert report["max_total_capital_at_risk"] == 70.0
    assert report["markout_loss_cancel_threshold"] == "-3c"
    assert report["live_order_path"] is False
    assert "live_order_path=false" in render_markdown(report)


def test_manual_kill_switch_ready_with_operator_confirmation():
    report = build_weather_lp_manual_kill_switch_checklist(
        position_sizing_report={},
        kill_switch_policy_report={"daily_stop_loss_ready": True},
        operator_confirmed=True,
    )

    assert report["ready"] is True
    assert report["checklist_status"] == "ready"
