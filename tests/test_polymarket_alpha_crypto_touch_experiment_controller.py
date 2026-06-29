from __future__ import annotations

from scripts.polymarket_alpha_crypto_touch_experiment_controller import build_crypto_touch_72h_experiment_report


def test_crypto_touch_72h_controller_keeps_threshold_when_near_miss_negative():
    report = build_crypto_touch_72h_experiment_report(
        crypto_probability_report={"candidate_count": 1},
        formal_fills=[
            {
                "market_slug": "btc-touch",
                "token_id": "yes-token",
                "side": "YES",
                "entry_time": "2026-06-29T00:00:00Z",
            }
        ],
        markout_report={"available_markout_count": 1, "by_horizon": [{"bucket": "3600s", "mean": -1.0}]},
        near_miss_watch=[{"watch_id": "watch-1", "entry_time": "2026-06-29T00:01:00Z"}],
        near_miss_markout_report={"by_horizon": [{"bucket": "300s", "mean": -1.5}]},
        surface_report={"rows": []},
        sensitivity_report={"rows": []},
        active_probability_report={"new_formal_fill_count": 1},
    )

    assert report["formal_fill_count"] == 1
    assert report["keep_threshold_do_not_lower"] is True
    assert report["recommendation"] == "reduce_priority_or_pause_after_more_samples"
    assert report["live_order_path"] is False
    assert report["promote_to_tiny_live_review_candidate_never_set_true_until_enough_resolved_markout"] is False


def test_crypto_touch_72h_controller_reduces_priority_when_negative_and_surface_unsupported():
    report = build_crypto_touch_72h_experiment_report(
        crypto_probability_report={},
        formal_fills=[{"market_slug": "btc-touch", "token_id": "yes-token", "side": "YES"}],
        markout_report={
            "available_markout_count": 2,
            "by_horizon": [
                {"bucket": "3600s", "mean": -1.0},
                {"bucket": "current", "mean": -2.0},
            ],
        },
        near_miss_watch=[],
        near_miss_markout_report={},
        surface_report={"rows": [{"market_slug": "btc-touch", "token_id": "yes-token", "side": "YES", "surface_supports_model_direction": False}]},
        sensitivity_report={},
        active_probability_report={},
    )

    assert report["surface_supported_fill_count"] == 0
    assert report["recommendation"] == "reduce_priority_or_pause_after_more_samples"


def test_crypto_touch_72h_controller_reduces_priority_when_all_negative_and_fragile():
    report = build_crypto_touch_72h_experiment_report(
        crypto_probability_report={},
        formal_fills=[{"market_slug": "btc-touch", "token_id": "yes-token", "side": "YES"}],
        markout_report={
            "available_markout_count": 2,
            "by_horizon": [
                {"bucket": "3600s", "mean": -1.0},
                {"bucket": "current", "mean": -2.0},
            ],
        },
        near_miss_watch=[],
        near_miss_markout_report={},
        surface_report={"rows": [{"market_slug": "btc-touch", "token_id": "yes-token", "side": "YES", "surface_supports_model_direction": True}]},
        sensitivity_report={"rows": [{"market_slug": "btc-touch", "token_id": "yes-token", "side": "YES", "sensitivity_fragile": True}]},
        active_probability_report={},
    )

    assert report["surface_supported_fill_count"] == 1
    assert report["sensitivity_fragile_fill_count"] == 1
    assert report["recommendation"] == "reduce_priority_or_pause_after_more_samples"
