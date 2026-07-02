from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_cancellation_policy import build_weather_lp_cancellation_policy_report


def test_cancellation_policy_compares_hour_boundary_and_hold():
    report = build_weather_lp_cancellation_policy_report(
        quotes=[{"quote_id": "q1", "city": "ankara", "quote_start_time": "2026-06-29T10:45:00Z"}],
        quote_updates=[
            {"quote_id": "q1", "update_time": "2026-06-29T10:50:00Z", "cumulative_reward_points_proxy": 1.0, "price_markout_from_entry": -0.2, "qualifies_for_reward": True},
            {"quote_id": "q1", "update_time": "2026-06-29T10:59:00Z", "cumulative_reward_points_proxy": 2.0, "price_markout_from_entry": -0.4, "qualifies_for_reward": True},
        ],
    )

    policies = {row["policy"]: row for row in report["by_policy"]}
    assert policies["cancel_at_hour_boundary"]["quote_count"] == 1
    assert policies["hold_full_window"]["quote_count"] == 1
    assert report["live_order_path"] is False


def test_cancellation_policy_flags_reward_disqualification():
    report = build_weather_lp_cancellation_policy_report(
        quotes=[{"quote_id": "q1", "quote_start_time": "2026-06-29T10:00:00Z"}],
        quote_updates=[
            {"quote_id": "q1", "update_time": "2026-06-29T10:05:00Z", "cumulative_reward_points_proxy": 1.0, "price_markout_from_entry": -0.5, "qualifies_for_reward": False, "non_qualification_reason": "spread_too_wide"},
        ],
    )

    row = [item for item in report["rows"] if item["policy"] == "cancel_after_reward_disqualified"][0]
    assert row["cancellation_reason"] == "spread_too_wide"
