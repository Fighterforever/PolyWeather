from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_reward_discovery import build_weather_lp_reward_discovery


def test_discovery_does_not_treat_maker_rebate_as_lp_reward():
    report = build_weather_lp_reward_discovery(
        family_catalog={"families": [{"buckets": [{"market_slug": "m", "market_id": "1"}]}]},
        reward_metadata_rows=[
            {
                "market_slug": "m",
                "reward_program_type": "maker_rebate",
                "gap_reason": "maker_rebate_only_not_liquidity_reward",
            }
        ],
    )

    assert report["reward_metadata_available_count"] == 0
    assert report["markets"][0]["reward_program_type"] == "maker_rebate"
    assert report["markets"][0]["gap_reason"] == "maker_rebate_only_not_liquidity_reward"
    assert report["live_order_path"] is False
