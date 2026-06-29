from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_quote_optimizer import build_weather_lp_quote_optimizer


def test_quote_optimizer_selects_reward_qualified_variant():
    report = build_weather_lp_quote_optimizer(
        reward_markets=[
            {
                "market_slug": "m",
                "token_id": "t",
                "city": "ankara",
                "station_code": "LTAC",
                "best_bid": 0.49,
                "best_ask": 0.51,
                "min_incentive_size": 50,
                "max_incentive_spread": 0.05,
                "reward_program_type": "liquidity_reward",
            }
        ],
        city_regimes=[{"city": "ankara", "volatility_bucket": "normal"}],
    )

    assert report["market_count"] == 1
    assert report["variant_count"] >= 1
    assert report["selected_quote_count"] == 1
    assert report["candidates"][0]["live_order_path"] is False


def test_quote_optimizer_rejects_expensive_basket():
    report = build_weather_lp_quote_optimizer(
        reward_markets=[
            {
                "market_slug": "m",
                "token_id": "t",
                "city": "ankara",
                "best_bid": 0.985,
                "best_ask": 0.995,
                "min_incentive_size": 50,
                "max_incentive_spread": 0.05,
                "reward_program_type": "liquidity_reward",
            }
        ],
        city_regimes=[{"city": "ankara", "volatility_bucket": "normal"}],
    )

    assert report["selected_quote_count"] == 0
    assert any(row["reason"] in {"extreme_midpoint_requires_two_sided_liquidity", "expensive_basket_near_full_payout"} for row in report["blocker_counts"])
