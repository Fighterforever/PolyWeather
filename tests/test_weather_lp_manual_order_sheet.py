from __future__ import annotations

import json

from src.trading.polymarket_alpha.weather_lp_manual_order_sheet import build_weather_lp_manual_order_sheet, load_jsonl, select_manual_payout_audit_quotes


def test_manual_order_sheet_filters_and_marks_no_auto_trade():
    report = build_weather_lp_manual_order_sheet(
        quote_optimizer_report={"selected_quote_count": 1},
        reward_vs_risk_report={},
        profitability_simulation_report={"scenario_table": {"base_net": 10, "conservative_net": -5}},
        position_sizing_report={"total_weather_lp_exposure_cap_dollars": 100},
        kill_switch_policy_report={"cancellation_policy_ready": True, "recommended_action": "paper_cancel_rules"},
        paper_quotes=[
            {
                "quote_id": "q1",
                "market_slug": "m1",
                "token_id": "t1",
                "side": "YES",
                "quote_price": 0.2,
                "quote_size": 20,
                "midpoint": 0.21,
                "min_incentive_size": 20,
                "max_incentive_spread": 0.05,
            }
        ],
        quote_updates=[{"quote_id": "q1", "update_time": "2026-07-01T00:00:00Z", "current_midpoint": 0.21}],
        profitability_rows=[
            {
                "quote_id": "q1",
                "visible_reward_share_proxy": 0.05,
                "cumulative_reward_points_proxy": 1.0,
                "observed_markout_cents": 1.0,
                "estimated_reward_cents_by_scenario": {"base": 5, "conservative": 1},
                "net_pnl_cents_by_scenario": {"conservative": -10},
                "basket_total_cost": 0.2,
            }
        ],
        position_sizing_rows=[{"quote_id": "q1", "recommended_paper_size": 10, "max_tiny_live_size_if_ever_allowed": 2, "max_loss_if_filled": 4, "basket_total_cost": 0.2}],
    )

    row = report["rows"][0]
    assert report["suggested_manual_quote_count"] == 1
    assert row["manual_review_required"] is True
    assert row["do_not_auto_trade"] is True
    assert row["live_order_path"] is False


def test_manual_order_sheet_rejects_expensive_basket():
    report = build_weather_lp_manual_order_sheet(
        quote_optimizer_report={},
        reward_vs_risk_report={},
        profitability_simulation_report={},
        position_sizing_report={},
        kill_switch_policy_report={"cancellation_policy_ready": True},
        paper_quotes=[{"quote_id": "q1"}],
        quote_updates=[],
        profitability_rows=[{"quote_id": "q1", "visible_reward_share_proxy": 0.1, "cumulative_reward_points_proxy": 1, "basket_total_cost": 0.99}],
        position_sizing_rows=[{"quote_id": "q1", "recommended_paper_size": 1, "basket_total_cost": 0.99}],
    )

    assert report["suggested_manual_quote_count"] == 0
    assert any(row["reason"] == "expensive_basket_near_full_payout" for row in report["rejection_reasons"])


def test_manual_audit_quote_selector_limits_to_three_and_25_dollars():
    rows = []
    impacts = []
    profits = []
    for idx, capital in enumerate([7.0, 8.0, 9.0, 12.0], start=1):
        market = f"market-{idx}"
        token = f"token-{idx}"
        rows.append(
            {
                "market_slug": market,
                "token_id": token,
                "city": "ankara",
                "side": "YES",
                "suggested_quote_price": 0.35,
                "suggested_quote_size": 5,
                "capital_at_risk": capital,
                "min_incentive_size": 20,
                "max_incentive_spread": 0.045,
                "quote_distance_from_midpoint": 0.002,
                "qualifies_for_reward": True,
                "visible_reward_share_proxy": 0.05,
                "base_scenario_reward": 6,
                "conservative_scenario_reward": 0.2,
                "current_markout": 0.1,
                "basket_total_cost": 0.4,
                "cancellation_rule": "cancel_at_hour_boundary",
            }
        )
        impacts.append({"market_slug": market, "token_id": token, "quote_would_be_resting": True, "quote_would_cross_or_take": False})
        profits.append({"market_slug": market, "token_id": token, "estimated_reward_cents_by_scenario": {"optimistic": 20}, "observed_markout_cents": 0.1})

    report = select_manual_payout_audit_quotes(manual_order_rows=rows, impact_rows=impacts, profitability_rows=profits)

    assert report["selected_quote_count"] == 3
    assert report["total_selected_capital_at_risk"] <= 25
    assert all(row["manual_execution_only"] is True for row in report["selected_quotes"])
    assert all(row["suggested_quote_size"] >= row["min_incentive_size"] for row in report["selected_quotes"])
    assert any("audit_quote_count_cap_reached" in row["rejected_reason"] for row in report["rejected_quotes"])


def test_manual_audit_quote_selector_rejects_crossing_quote():
    report = select_manual_payout_audit_quotes(
        manual_order_rows=[
            {
                "market_slug": "m1",
                "token_id": "t1",
                "suggested_quote_price": 0.4,
                "capital_at_risk": 8,
                "min_incentive_size": 20,
                "max_incentive_spread": 0.05,
                "qualifies_for_reward": True,
                "visible_reward_share_proxy": 0.1,
                "base_scenario_reward": 4,
                "current_markout": 0,
                "cancellation_rule": "cancel",
            }
        ],
        impact_rows=[{"market_slug": "m1", "token_id": "t1", "quote_would_be_resting": False, "quote_would_cross_or_take": True}],
    )

    assert report["selected_quote_count"] == 0
    assert "quote_would_cross_or_take" in report["rejected_quotes"][0]["rejected_reason"]
    assert report["live_order_path"] is False


def test_manual_order_sheet_loader_accepts_json_array(tmp_path):
    path = tmp_path / "manual_sheet.json"
    path.write_text(json.dumps([{"quote_id": "q1"}]), encoding="utf-8")

    assert load_jsonl(path) == [{"quote_id": "q1"}]
