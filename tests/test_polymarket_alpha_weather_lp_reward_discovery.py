from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_reward_discovery import build_weather_lp_reward_discovery


def _catalog(bucket: dict) -> dict:
    return {"families": [{"event_slug": "e", "buckets": [bucket]}]}


def test_reward_discovery_reports_metadata_gap_without_faking_reward():
    report = build_weather_lp_reward_discovery(
        family_catalog=_catalog({"market_slug": "m", "city": "ankara", "yes_best_ask": 0.1}),
        generated_at="2026-06-29T10:45:00Z",
    )

    assert report["market_count"] == 1
    assert report["reward_market_count"] == 0
    assert report["markets"][0]["gap_reason"] == "no_reward_fields_in_response"
    assert report["live_order_path"] is False


def test_reward_discovery_detects_available_reward():
    report = build_weather_lp_reward_discovery(
        family_catalog=_catalog(
            {
                "market_slug": "m",
                "city": "ankara",
                "yes_token_id": "yes-token",
                "no_token_id": "no-token",
                "yes_best_ask": 0.1,
                "no_best_ask": 0.91,
            }
        ),
        reward_metadata_rows=[
            {
                "market_slug": "m",
                "reward_program_type": "liquidity_reward",
                "min_incentive_size": 50,
                "max_incentive_spread": 0.04,
                "source_found": "clob_market_full_object",
            }
        ],
        generated_at="2026-06-29T10:45:00Z",
    )

    assert report["reward_market_count"] == 1
    assert report["reward_metadata_available_count"] == 1
    assert report["min_max_incentive_found_count"] == 1
    assert report["markets"][0]["reward_window_detected"] is True
    assert report["markets"][0]["token_id"] == "yes-token"
    assert report["markets"][0]["no_token_id"] == "no-token"
