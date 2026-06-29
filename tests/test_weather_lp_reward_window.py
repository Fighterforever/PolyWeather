from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_reward_window import build_weather_lp_reward_window_report


def test_reward_window_does_not_assume_40_51_without_observations():
    report = build_weather_lp_reward_window_report([])

    assert report["confidence"] == "insufficient_observations"
    assert report["40_to_51_minute_support_count"] == 0
    assert report["recommendation"] == "insufficient_observations"


def test_reward_window_counts_40_51_support():
    rows = [{"generated_at": f"2026-06-29T10:{minute:02d}:00Z", "reward_metadata_available_count": 1, "reward_qualified_quote_count": 1} for minute in range(40, 52)]
    report = build_weather_lp_reward_window_report(rows)

    assert report["40_to_51_minute_support_count"] == 12
    assert report["candidate_reward_window"] == "40-51"
    assert report["recommendation"] == "40_51_window_supported"
