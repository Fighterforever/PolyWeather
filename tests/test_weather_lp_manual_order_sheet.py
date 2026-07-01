from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_manual_order_sheet import build_weather_lp_manual_order_sheet


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
