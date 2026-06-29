from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_basket_risk import evaluate_weather_lp_basket


def test_rejects_98_percent_basket():
    report = evaluate_weather_lp_basket(legs=[{"entry_price": 0.49}, {"entry_price": 0.49}])

    assert report["decision"] == "reject"
    assert report["expensive_basket_blocker"] == "expensive_basket_near_full_payout"


def test_stable_city_prefers_narrow_range():
    report = evaluate_weather_lp_basket(legs=[{"entry_price": 0.1}] * 4, city_volatility_bucket="stable")

    assert "stable_city_prefers_narrow_range" in report["blockers"]


def test_volatile_city_allows_wider_range_with_reward_buffer():
    report = evaluate_weather_lp_basket(legs=[{"entry_price": 0.05}] * 4, city_volatility_bucket="volatile", reward_estimate=0.05)

    assert report["decision"] == "pass"


def test_reward_not_counted_as_guaranteed_pnl():
    report = evaluate_weather_lp_basket(legs=[{"entry_price": 0.2}], reward_estimate=0.1)

    assert report["reward_counted_as_guaranteed_pnl"] is False
    assert report["live_order_path"] is False
