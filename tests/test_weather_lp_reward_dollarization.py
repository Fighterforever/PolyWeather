from __future__ import annotations

import pytest

from src.trading.polymarket_alpha.weather_lp_reward_dollarization import build_weather_lp_reward_dollarization_report


def test_dollarization_exact_unavailable_outputs_scenarios():
    report = build_weather_lp_reward_dollarization_report(
        paper_quotes=[
            {
                "quote_id": "q1",
                "market_slug": "m1",
                "quote_start_time": "2026-07-01T00:00:00Z",
                "quote_size": 100,
                "quote_price": 0.2,
                "city": "ankara",
            }
        ],
        quote_updates=[
            {
                "quote_id": "q1",
                "market_slug": "m1",
                "update_time": "2026-07-01T01:00:00Z",
                "cumulative_reward_points_proxy": 5,
                "qualifies_for_reward": True,
            }
        ],
        reward_metadata_audit_report={"rows": [{"market_slug": "m1"}]},
        reward_share_estimator_report={"visible_reward_share_median": 0.01},
        reward_vs_risk_report={},
        reward_allocation_audit_report={"rows": [{"market_slug": "m1", "reward_allocation_raw": None}]},
        reward_share_rows=[{"quote_id": "q1", "our_visible_reward_share_proxy": 0.01}],
        reward_vs_risk_rows=[{"quote_id": "q1", "horizon": "current", "markout_cents": 0.5}],
    )

    assert report["exact_reward_cents_available_count"] == 0
    assert report["scenario_total_reward_if_daily_allocation_10"] > 0
    assert report["rows"][0]["exact"] is False
    assert report["live_order_path"] is False


def test_visible_share_proxy_not_marked_exact():
    report = build_weather_lp_reward_dollarization_report(
        paper_quotes=[{"quote_id": "q1", "market_slug": "m1", "quote_start_time": "2026-07-01T00:00:00Z", "quote_size": 10, "quote_price": 0.2}],
        quote_updates=[{"quote_id": "q1", "update_time": "2026-07-01T00:30:00Z"}],
        reward_metadata_audit_report={"rows": [{"market_slug": "m1"}]},
        reward_share_estimator_report={},
        reward_vs_risk_report={},
        reward_allocation_audit_report={"rows": [{"market_slug": "m1", "reward_allocation_raw": 10}]},
        reward_share_rows=[{"quote_id": "q1", "our_visible_reward_share_proxy": 0.05}],
        reward_vs_risk_rows=[],
    )

    row = report["rows"][0]
    assert row["visible_proxy_reward_cents"] is not None
    assert row["exact_reward_cents"] is None
    assert row["confidence"] == "visible_orderbook_proxy"


def test_break_even_share_for_negative_markout():
    report = build_weather_lp_reward_dollarization_report(
        paper_quotes=[{"quote_id": "q1", "market_slug": "m1", "quote_start_time": "2026-07-01T00:00:00Z", "quote_size": 100, "quote_price": 0.2}],
        quote_updates=[{"quote_id": "q1", "update_time": "2026-07-01T12:00:00Z"}],
        reward_metadata_audit_report={},
        reward_share_estimator_report={},
        reward_vs_risk_report={},
        reward_allocation_audit_report={},
        reward_share_rows=[{"quote_id": "q1", "our_visible_reward_share_proxy": 0.01}],
        reward_vs_risk_rows=[{"quote_id": "q1", "horizon": "current", "markout_cents": -1.0}],
    )

    row = report["rows"][0]
    assert row["break_even_share_for_minus_1c"] == 0.2
    assert row["break_even_daily_allocation_for_minus_3c_stress"] == 600.0


def test_scenario_reward_scales_with_allocation_and_time():
    base_kwargs = dict(
        paper_quotes=[{"quote_id": "q1", "market_slug": "m1", "quote_start_time": "2026-07-01T00:00:00Z", "quote_size": 10, "quote_price": 0.2}],
        reward_metadata_audit_report={},
        reward_share_estimator_report={},
        reward_vs_risk_report={},
        reward_allocation_audit_report={},
        reward_share_rows=[{"quote_id": "q1", "our_visible_reward_share_proxy": 0.01}],
        reward_vs_risk_rows=[],
    )
    one_hour = build_weather_lp_reward_dollarization_report(
        quote_updates=[{"quote_id": "q1", "update_time": "2026-07-01T01:00:00Z"}],
        **base_kwargs,
    )
    two_hours = build_weather_lp_reward_dollarization_report(
        quote_updates=[{"quote_id": "q1", "update_time": "2026-07-01T02:00:00Z"}],
        **base_kwargs,
    )

    assert two_hours["scenario_total_reward_if_daily_allocation_10"] == pytest.approx(one_hour["scenario_total_reward_if_daily_allocation_10"] * 2)


def test_no_live_order_path():
    report = build_weather_lp_reward_dollarization_report(
        paper_quotes=[],
        quote_updates=[],
        reward_metadata_audit_report={},
        reward_share_estimator_report={},
        reward_vs_risk_report={},
        reward_allocation_audit_report={},
    )

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False
