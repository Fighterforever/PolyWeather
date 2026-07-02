from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_kill_switch_policy import build_weather_lp_kill_switch_policy_report


def test_kill_switch_flags_stress_and_missing_manual_switch():
    report = build_weather_lp_kill_switch_policy_report(
        reward_risk_report={"mean_current_markout": 0.5},
        profitability_simulation_report={"stress_table": {"minus_3c": -100.0}, "scenario_table": {"base_net": 50.0}},
        position_sizing_report={"city_exposure_cap_ready": True, "max_total_capital_at_risk_ready": True},
    )

    assert report["recommended_action"] == "tighten_cancellation_and_sizing_for_minus_3c_stress"
    assert "manual_kill_switch_ready" in report["missing_controls"]
    assert report["daily_stop_loss_ready"] is True
    assert report["live_order_path"] is False
