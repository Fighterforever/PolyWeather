from __future__ import annotations

from scripts.polymarket_alpha_weather_lp_manual_review_packet import build_manual_review_packet, render_markdown


def test_manual_review_packet_requires_payout_audit_but_not_live():
    packet = build_manual_review_packet(
        manual_order_sheet_report={
            "suggested_manual_quote_count": 2,
            "total_capital_at_risk_if_all_manual_quotes_used": 12.5,
            "base_scenario_net_cents": 100.0,
            "conservative_scenario_net_cents": -30.0,
        },
        impact_simulator_report={"impact_simulation_ready": True},
        profitability_simulation_report={"exact_reward_available": False, "observed_markout_total_cents": 20.0},
        reward_risk_report={"mean_current_markout": 0.5},
        reward_share_report={"visible_reward_share_median": 0.08},
        reward_payout_audit_report={"audit_status": "manual_audit_required", "exact_reward_available": False},
        manual_kill_switch_checklist={"checklist_status": "ready", "ready": True},
        tiny_live_gap_report={"exact_reward_available": False},
        manual_order_sheet_path="evidence/weather_lp_rewards/manual_tiny_live_order_sheet.csv",
        generated_at="2026-07-01T00:00:00Z",
    )

    assert packet["manual_review_packet_ready"] is True
    assert packet["suggested_human_decision"] == "prepare_manual_tiny_live_review_but_reward_payout_audit_required"
    assert "exact_reward_payout_not_verified" in packet["remaining_blockers"]
    assert packet["do_not_auto_trade"] is True
    assert packet["live_ready"] is False
    assert packet["live_order_path"] is False
    rendered = render_markdown(packet)
    assert "no automatic trading" in rendered
    assert "live_order_path=false" in rendered


def test_manual_review_packet_blocks_when_kill_switch_missing():
    packet = build_manual_review_packet(
        manual_order_sheet_report={"suggested_manual_quote_count": 1},
        impact_simulator_report={"impact_simulation_ready": True},
        profitability_simulation_report={"exact_reward_available": True},
        reward_risk_report={},
        reward_share_report={},
        reward_payout_audit_report={"audit_status": "payout_observed", "exact_reward_available": True},
        manual_kill_switch_checklist={"checklist_status": "partial", "ready": False},
        tiny_live_gap_report={},
        manual_order_sheet_path="manual.csv",
    )

    assert packet["suggested_human_decision"] == "do_not_live_kill_switch_missing"
    assert "manual_kill_switch_not_ready" in packet["remaining_blockers"]
    assert packet["live_order_path"] is False


def test_manual_review_packet_blocks_when_manual_capital_exceeds_kill_limit():
    packet = build_manual_review_packet(
        manual_order_sheet_report={"suggested_manual_quote_count": 2, "total_capital_at_risk_if_all_manual_quotes_used": 75.0},
        impact_simulator_report={"impact_simulation_ready": True},
        profitability_simulation_report={"exact_reward_available": True},
        reward_risk_report={},
        reward_share_report={},
        reward_payout_audit_report={"audit_status": "payout_observed", "exact_reward_available": True},
        manual_kill_switch_checklist={"checklist_status": "ready", "ready": True, "max_total_capital_at_risk": 70.0},
        tiny_live_gap_report={},
        manual_order_sheet_path="manual.csv",
    )

    assert packet["suggested_human_decision"] == "do_not_live_manual_quote_capital_exceeds_kill_switch_limit"
    assert "manual_quote_capital_exceeds_kill_switch_limit" in packet["remaining_blockers"]
    assert packet["live_order_path"] is False
