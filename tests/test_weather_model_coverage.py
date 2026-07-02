from __future__ import annotations

from src.trading.weather_model_coverage import build_weather_model_coverage_report


def test_model_coverage_ranks_non_temperature_tradeable_model_gap():
    payload = {
        "snapshot_id": "weather-scan",
        "rows": [
            {
                "market_family": "rain",
                "model_join_status": "unsupported_market_type",
                "event_title": "Will it rain in New York?",
                "market_slug": "will-it-rain-in-new-york-on-june-27",
                "token_id": "rain-token",
                "side": "yes",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.35,
                "spread": 0.01,
                "execution_liquidity": 1200,
                "bid_depth_usdc_3c": 40,
                "ask_depth_usdc_3c": 50,
                "end_date": "2026-07-03T00:00:00Z",
            },
            {
                "market_family": "temperature",
                "model_join_status": "joined",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.20,
                "spread": 0.01,
                "execution_liquidity": 500,
                "bid_depth_usdc_3c": 30,
                "ask_depth_usdc_3c": 30,
            },
        ],
    }

    report = build_weather_model_coverage_report(payload, generated_at="2026-06-27T00:00:00Z")

    assert report["schema_version"] == "polyweather_weather_model_coverage.v1"
    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["hard_conclusion"] == "non_temperature_model_gap_detected"
    assert report["model_gap_surface_ready_count"] == 1
    assert report["model_build_queue"] == [
        {
            "market_family": "rain",
            "model_gap_surface_ready_count": 1,
            "row_count": 1,
            "mean_model_gap_liquidity": 1200.0,
            "median_model_gap_spread": 0.01,
            "max_model_build_horizon_days": 14.0,
            "recommended_action": "build_precipitation_probability_model",
            "samples": [
                {
                    "market_family": "rain",
                    "model_join_status": "unsupported_market_type",
                    "event_title": "Will it rain in New York?",
                    "question": None,
                    "market_id": None,
                    "market_slug": "will-it-rain-in-new-york-on-june-27",
                    "token_id": "rain-token",
                    "side": "yes",
                    "outcome": None,
                    "price": 0.35,
                    "spread": 0.01,
                    "liquidity": 1200.0,
                    "bid_depth_usdc_3c": 40.0,
                    "ask_depth_usdc_3c": 50.0,
                    "end_date": "2026-07-03T00:00:00Z",
                    "surface_blockers": [],
                    "horizon_days": 6.0,
                }
            ],
        }
    ]
    assert report["long_horizon_watch_queue"] == []


def test_model_coverage_keeps_thin_unsupported_market_out_of_build_queue():
    payload = {
        "rows": [
            {
                "market_family": "hurricane",
                "model_join_status": "unsupported_market_type",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.40,
                "spread": 0.09,
                "execution_liquidity": 2,
                "bid_depth_usdc_3c": 0,
                "ask_depth_usdc_3c": 0,
            }
        ]
    }

    report = build_weather_model_coverage_report(payload)

    assert report["hard_conclusion"] == "non_temperature_surface_not_tradeable_yet"
    assert report["model_build_queue"] == []
    family = report["family_coverage"][0]
    assert family["market_family"] == "hurricane"
    assert family["model_gap_surface_ready_count"] == 0
    assert {row["reason"] for row in family["blocked_surface_reason_counts"]} == {
        "ask_depth_below_min",
        "bid_depth_below_min",
        "liquidity_below_min",
        "spread_above_max",
    }


def test_model_coverage_routes_tradeable_long_horizon_gap_to_watch_queue():
    payload = {
        "rows": [
            {
                "market_family": "hurricane",
                "model_join_status": "unsupported_market_type",
                "market_slug": "will-any-category-4-hurricane-make-landfall-in-the-us-in-before-2027",
                "active": True,
                "closed": False,
                "tradable": True,
                "accepting_orders": True,
                "price": 0.34,
                "spread": 0.01,
                "execution_liquidity": 60,
                "bid_depth_usdc_3c": 20,
                "ask_depth_usdc_3c": 65,
                "end_date": "2026-12-31T00:00:00Z",
            }
        ]
    }

    report = build_weather_model_coverage_report(
        payload,
        generated_at="2026-06-27T00:00:00Z",
        max_model_build_horizon_days=14,
    )

    assert report["hard_conclusion"] == "non_temperature_model_gap_long_horizon_only"
    assert report["model_build_queue"] == []
    assert report["long_horizon_watch_queue_count"] == 1
    assert report["long_horizon_watch_queue"][0]["market_family"] == "hurricane"
    assert report["long_horizon_watch_queue"][0]["samples"][0]["horizon_days"] == 187.0
