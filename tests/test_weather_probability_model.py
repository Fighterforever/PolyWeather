from __future__ import annotations

from src.trading.weather_probability_model import build_weather_probability_estimate


def test_build_weather_probability_estimate_uses_executable_ask_and_costs():
    estimate = build_weather_probability_estimate(
        {
            "best_ask": 0.52,
            "spread": 0.02,
            "fee_cost": 0.005,
            "slippage_cost": 0.002,
        },
        model_probability=0.70,
    )

    payload = estimate.to_dict()
    assert payload["schema_version"] == "polyweather_weather_probability_estimate.v1"
    assert payload["p_model"] == 0.70
    assert payload["p_lcb"] == 0.67
    assert payload["q_effective"] == 0.52
    assert payload["executable_price_source"] == "best_ask"
    assert payload["cost"] == 0.007
    assert payload["ev_safe"] == 0.143


def test_build_weather_probability_estimate_without_price_has_no_ev_safe():
    estimate = build_weather_probability_estimate({}, model_probability=0.70)

    assert estimate.q_effective is None
    assert estimate.ev_safe is None
