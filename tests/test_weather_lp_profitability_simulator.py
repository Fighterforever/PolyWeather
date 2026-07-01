from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_profitability_simulator import build_weather_lp_profitability_simulation


def test_profitability_simulation_outputs_scenario_net_and_roi():
    report = build_weather_lp_profitability_simulation(
        paper_quotes=[
            {
                "quote_id": "q1",
                "market_slug": "m1",
                "city": "ankara",
                "station_code": "LTAC",
                "side": "YES",
                "quote_price": 0.25,
                "quote_size": 100,
                "strategy_variant": "single_sided_low_risk_quote",
                "basket_cost": 0.25,
            }
        ],
        quote_updates=[],
        reward_vs_risk_report={},
        reward_share_estimator_report={"visible_reward_share_median": 0.5},
        reward_dollarization_report={},
        reward_allocation_audit_report={},
        weather_lp_experiment_report={},
        reward_dollarization_rows=[
            {
                "quote_id": "q1",
                "quote_price": 0.25,
                "quote_size": 100,
                "time_fraction": 0.5,
                "our_visible_share_proxy": 0.5,
                "observed_markout_total_cents": 25.0,
                "cumulative_reward_points_proxy": 10.0,
            }
        ],
    )

    assert report["quote_count"] == 1
    assert report["scenario_table"]["conservative_net"] == -297.5
    assert report["scenario_table"]["base_net"] == 87.5
    assert report["roi_table"]["base"] == 0.035
    assert report["stress_table"]["minus_3c"] == -237.5
    assert report["live_order_path"] is False


def test_profitability_simulation_exact_reward_flag_from_count():
    report = build_weather_lp_profitability_simulation(
        paper_quotes=[],
        quote_updates=[],
        reward_vs_risk_report={},
        reward_share_estimator_report={},
        reward_dollarization_report={"exact_reward_cents_available_count": 1},
        reward_allocation_audit_report={},
        weather_lp_experiment_report={},
    )

    assert report["exact_reward_available"] is True
    assert report["paper_only"] is True
