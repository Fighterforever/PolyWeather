from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_live_impact_simulator import build_weather_lp_live_impact_simulator_report


def test_impact_simulator_rejects_crossing_quote():
    report = build_weather_lp_live_impact_simulator_report(
        manual_order_rows=[{"market_slug": "m", "token_id": "t", "suggested_quote_price": 0.52, "suggested_quote_size": 2, "visible_reward_share_proxy": 0.05}],
        quote_updates=[{"token_id": "t", "update_time": "2026-07-01T00:00:00Z", "current_best_bid": 0.49, "current_best_ask": 0.51, "current_midpoint": 0.5}],
    )

    row = report["rows"][0]
    assert row["quote_would_cross_or_take"] is True
    assert row["quote_would_be_resting"] is False
    assert row["impact_warning"] == "quote_would_cross_or_take"
    assert report["live_order_path"] is False


def test_impact_simulator_accepts_resting_quote():
    report = build_weather_lp_live_impact_simulator_report(
        manual_order_rows=[{"market_slug": "m", "token_id": "t", "suggested_quote_price": 0.495, "suggested_quote_size": 2, "visible_reward_share_proxy": 0.05}],
        quote_updates=[{"token_id": "t", "update_time": "2026-07-01T00:00:00Z", "current_best_bid": 0.49, "current_best_ask": 0.51, "current_midpoint": 0.5}],
    )

    assert report["accepted_resting_quote_count"] == 1
    assert report["rows"][0]["qualifies_for_reward_after_insert"] is False
    assert report["rows"][0]["live_order_path"] is False
