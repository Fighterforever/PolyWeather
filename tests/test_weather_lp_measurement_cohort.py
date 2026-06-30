from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_measurement_cohort import (
    build_weather_lp_cohort_markout_rows,
    build_weather_lp_measurement_cohort_report,
)


def test_measurement_cohort_created_for_active_quotes():
    report = build_weather_lp_measurement_cohort_report(
        quotes=[
            {
                "quote_id": "q1",
                "stable_quote_key": "stable",
                "market_slug": "m",
                "token_id": "t",
                "city": "ankara",
                "quote_price": 0.49,
                "quote_size": 50,
                "quote_status": "active",
            }
        ],
        quote_updates=[
            {
                "quote_id": "q1",
                "update_time": "2026-06-30T10:00:00Z",
                "current_midpoint": 0.5,
                "current_best_bid": 0.49,
                "current_best_ask": 0.51,
                "cumulative_reward_points_proxy": 7.0,
            }
        ],
        generated_at="2026-06-30T10:00:00Z",
    )

    cohort = report["cohorts"][0]
    assert report["active_quote_count"] == 1
    assert report["active_cohort_count"] == 1
    assert report["cohorts_created_count"] == 1
    assert cohort["entry_midpoint_at_cohort_start"] == 0.5
    assert report["next_expected_5m_markout_time"] == "2026-06-30T10:05:00Z"
    assert report["live_order_path"] is False


def test_measurement_cohort_does_not_duplicate_young_active_cohort():
    report = build_weather_lp_measurement_cohort_report(
        quotes=[{"quote_id": "q1", "stable_quote_key": "stable", "quote_status": "active"}],
        quote_updates=[],
        existing_cohorts=[
            {
                "cohort_id": "c1",
                "quote_id": "q1",
                "stable_quote_key": "stable",
                "cohort_start_time": "2026-06-30T10:00:00Z",
                "status": "active",
            }
        ],
        generated_at="2026-06-30T10:10:00Z",
    )

    assert report["cohorts_created_count"] == 0
    assert report["cohorts_already_existing_count"] == 1
    assert report["active_cohort_count"] == 1


def test_cohort_markout_waits_until_horizon_is_old_enough():
    rows = build_weather_lp_cohort_markout_rows(
        cohorts=[
            {
                "cohort_id": "c1",
                "quote_id": "q1",
                "stable_quote_key": "stable",
                "cohort_start_time": "2026-06-30T10:00:00Z",
                "entry_midpoint_at_cohort_start": 0.5,
                "quote_price": 0.49,
                "status": "active",
            }
        ],
        quote_updates=[{"quote_id": "q1", "update_time": "2026-06-30T10:02:00Z", "current_midpoint": 0.51, "cumulative_reward_points_proxy": 1.0}],
    )

    status = {row["horizon"]: row["horizon_match_status"] for row in rows}
    assert status["5m"] == "cohort_not_old_enough"
    assert status["current"] == "current_latest"


def test_cohort_markout_uses_update_within_horizon_tolerance():
    rows = build_weather_lp_cohort_markout_rows(
        cohorts=[
            {
                "cohort_id": "c1",
                "quote_id": "q1",
                "stable_quote_key": "stable",
                "cohort_start_time": "2026-06-30T10:00:00Z",
                "entry_midpoint_at_cohort_start": 0.5,
                "quote_price": 0.49,
                "cumulative_reward_points_at_start": 2.0,
                "status": "active",
            }
        ],
        quote_updates=[
            {"quote_id": "q1", "update_time": "2026-06-30T10:05:30Z", "current_midpoint": 0.52, "cumulative_reward_points_proxy": 3.5}
        ],
    )

    row = [item for item in rows if item["horizon"] == "5m"][0]
    assert row["horizon_match_status"] == "within_tolerance"
    assert row["markout_from_cohort_midpoint"] == 2.0
    assert row["markout_from_quote_price"] == 3.0
    assert row["reward_points_increment_since_cohort_start"] == 1.5
