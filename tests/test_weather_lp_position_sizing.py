from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_position_sizing import build_weather_lp_position_sizing_report


def test_position_sizing_rejects_expensive_basket():
    report = build_weather_lp_position_sizing_report(
        quotes=[{"quote_id": "q1", "quote_price": 0.99, "quote_size": 20, "basket_cost": 0.99, "min_incentive_size": 1}]
    )

    row = report["rows"][0]
    assert row["rejected"] is True
    assert row["recommended_paper_size"] == 0.0
    assert row["reason"] == "expensive_basket_near_full_payout"
    assert report["live_order_path"] is False


def test_position_sizing_caps_total_and_tiny_live_placeholder():
    report = build_weather_lp_position_sizing_report(
        quotes=[{"quote_id": "q1", "quote_price": 0.5, "quote_size": 100, "basket_cost": 0.5, "min_incentive_size": 1}],
        per_market_risk_cap_dollars=10,
        tiny_live_per_market_cap_dollars=2,
    )

    row = report["rows"][0]
    assert row["recommended_paper_size"] == 20.0
    assert row["max_tiny_live_size_if_ever_allowed"] == 4.0
    assert report["recommended_total_capital_at_risk"] == 10.0
    assert row["live_order_path"] is False
