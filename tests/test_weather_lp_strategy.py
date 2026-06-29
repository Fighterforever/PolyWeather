from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_strategy import build_weather_lp_strategy


def test_strategy_blocks_when_reward_metadata_missing():
    report = build_weather_lp_strategy(
        reward_markets=[{"market_slug": "m", "city": "ankara", "best_ask": 0.02, "gap_reason": "reward_metadata_missing"}],
        city_regimes=[{"city": "ankara", "volatility_bucket": "stable"}],
    )

    assert report["candidate_count"] == 0
    assert report["reject_count"] == 1


def test_strategy_generates_paper_quote_when_gates_pass():
    report = build_weather_lp_strategy(
        reward_markets=[{"market_slug": "m", "city": "ankara", "best_ask": 0.02, "reward_available": True, "reward_score": 0.03}],
        city_regimes=[{"city": "ankara", "volatility_bucket": "stable"}],
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["decision"] == "paper_quote"
    assert report["candidates"][0]["live_order_path"] is False
