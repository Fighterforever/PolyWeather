from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_manual_kill_switch import (
    build_weather_lp_manual_kill_switch_checklist,
    render_markdown,
)
from scripts.polymarket_alpha_weather_lp_manual_execution_checklist import build_manual_execution_checklist, render_markdown as render_execution_markdown


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


def test_manual_execution_checklist_is_human_readable_and_no_api():
    report = build_manual_execution_checklist(
        selected_quotes_report={"selected_quotes": [{"rank": 1, "market_slug": "m1", "side": "YES", "suggested_quote_price": 0.4, "suggested_quote_size": 20, "capital_at_risk": 8}]},
        manual_payout_audit_plan={"cancel_rules": {"cancel_on_reward_disqualified": True}},
        manual_kill_switch_checklist={"ready": True},
    )

    assert "confirm AI/bot will not place orders" in report["before_placing"]
    assert "record midpoint / best bid / best ask every 5 minutes" in report["during_test"]
    assert report["no_api_order_placement"] is True
    assert report["live_order_path"] is False
    rendered = render_execution_markdown(report)
    assert "Manual UI only" in rendered
    assert "live_order_path=false" in rendered
