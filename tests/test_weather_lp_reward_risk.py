from __future__ import annotations

from src.trading.polymarket_alpha.weather_lp_reward_risk import (
    build_weather_lp_reward_risk_report,
    build_weather_lp_reward_share_report,
)


def test_reward_risk_keeps_reward_points_separate_from_cents():
    report = build_weather_lp_reward_risk_report(
        quotes=[
            {
                "quote_id": "q1",
                "city": "ankara",
                "strategy_variant": "single",
                "bucket_type": "eq",
                "basket_cost": 0.5,
                "quote_price": 0.49,
                "quote_start_time": "2026-06-29T10:00:00Z",
            }
        ],
        quote_updates=[
            {
                "quote_id": "q1",
                "update_time": "2026-06-29T10:05:00Z",
                "cumulative_reward_points_proxy": 2.0,
                "price_markout_from_entry": -1.0,
            }
        ],
    )

    row = report["quote_summaries"][0]
    markout_row = report["rows"][0]
    assert report["paper_quote_count"] == 1
    assert report["cumulative_reward_points_proxy"] == 2.0
    assert report["estimated_reward_cents_proxy"] is None
    assert row["markout_5m"] == -1.0
    assert markout_row["stable_quote_key"] is not None
    assert markout_row["markout_from_quote_price"] == -1.0
    assert report["valid_markout_count_by_horizon"][0] == {"horizon": "5m", "count": 1}
    assert report["reward_to_risk_proxy"] == 2.0
    assert report["live_order_path"] is False


def test_city_basket_attribution_outputs_cost_buckets():
    report = build_weather_lp_reward_risk_report(
        quotes=[
            {"quote_id": "q1", "city": "ankara", "basket_cost": 0.99, "quote_start_time": "2026-06-29T10:00:00Z"},
            {"quote_id": "q2", "city": "london", "basket_cost": 0.85, "quote_start_time": "2026-06-29T10:00:00Z"},
        ],
        quote_updates=[
            {"quote_id": "q1", "update_time": "2026-06-29T10:05:00Z", "price_markout_from_entry": -2.0, "cumulative_reward_points_proxy": 1.0},
            {"quote_id": "q2", "update_time": "2026-06-29T10:05:00Z", "price_markout_from_entry": 0.5, "cumulative_reward_points_proxy": 1.0},
        ],
    )

    attribution = report["city_basket_attribution"]
    buckets = {row["basket_cost_bucket"] for row in attribution["by_basket_cost_bucket"]}
    assert ">=0.98" in buckets
    assert "0.80-0.90" in buckets
    assert attribution["live_order_path"] is False


def test_reward_risk_does_not_reuse_current_update_for_missing_horizon():
    report = build_weather_lp_reward_risk_report(
        quotes=[{"quote_id": "q1", "quote_start_time": "2026-06-29T10:00:00Z", "quote_price": 0.5}],
        quote_updates=[{"quote_id": "q1", "update_time": "2026-06-29T11:00:00Z", "price_markout_from_entry": 1.0, "current_midpoint": 0.51}],
    )

    status_by_horizon = {row["horizon"]: row["horizon_match_status"] for row in report["rows"]}
    assert status_by_horizon["5m"] == "missing_update_for_horizon"
    assert status_by_horizon["15m"] == "missing_update_for_horizon"
    assert status_by_horizon["1h"] == "within_tolerance"
    assert status_by_horizon["current"] == "current_latest"


def test_reward_share_estimator_uses_visible_orderbook_proxy_only():
    report = build_weather_lp_reward_share_report(
        quotes=[
            {
                "quote_id": "q1",
                "market_slug": "m",
                "token_id": "t",
                "quote_price": 0.49,
                "quote_size": 50,
                "q_min_proxy": 10.0,
                "quote_status": "active",
            }
        ],
        reward_markets=[
            {
                "market_slug": "m",
                "token_id": "t",
                "best_bid": 0.49,
                "best_ask": 0.51,
                "bid_depth": 50,
                "ask_depth": 50,
                "min_incentive_size": 50,
                "max_incentive_spread": 0.05,
                "reward_allocation": 100.0,
            }
        ],
        quote_updates=[{"quote_id": "q1", "q_min_proxy": 10.0, "markout_from_quote_price": -0.5}],
    )

    row = report["rows"][0]
    assert report["visible_competitor_score_available_count"] == 1
    assert row["data_source"] == "visible_orderbook_proxy"
    assert row["our_visible_reward_share_proxy"] is not None
    assert row["estimated_reward_cents_proxy"] is not None
    assert report["live_order_path"] is False
