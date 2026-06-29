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
    assert report["markets"][0]["gap_reason"] == "reward_metadata_missing"
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
                "reward_rate_raw": 0.02,
            }
        ),
        generated_at="2026-06-29T10:45:00Z",
    )

    assert report["reward_market_count"] == 1
    assert report["markets"][0]["reward_window_detected"] is True
    assert report["markets"][0]["token_id"] == "yes-token"
    assert report["markets"][0]["no_token_id"] == "no-token"
