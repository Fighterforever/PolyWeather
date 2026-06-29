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
    assert report["recommendation"] == "shadow_only_pending_recalibration"
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
    assert report["recommendation"] == "shadow_only_pending_recalibration"


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
    assert report["recommendation"] == "shadow_only_pending_recalibration"


def test_crypto_touch_72h_controller_count_consistency_uses_existing_and_new_formal_counts():
    report = build_crypto_touch_72h_experiment_report(
        crypto_probability_report={"candidate_count": 2},
        formal_fills=[
            {"market_slug": "btc-a", "token_id": "a", "side": "YES"},
            {"market_slug": "btc-b", "token_id": "b", "side": "NO"},
            {"market_slug": "btc-c", "token_id": "c", "side": "YES", "invalidated": True},
        ],
        markout_report={},
        near_miss_watch=[{"watch_id": "near-1"}],
        near_miss_markout_report={},
        surface_report={},
        sensitivity_report={},
        active_probability_report={"old_formal_fill_count": 2, "new_formal_fill_count": 0, "watch_count": 7},
    )

    assert report["formal_fill_count"] == 3
    assert report["existing_formal_fill_count"] == 3
    assert report["old_formal_fill_count"] == 3
    assert report["valid_formal_fill_count"] == 2
    assert report["new_formal_fill_count"] == 0
    assert report["shadow_watch_count"] == 7
    assert report["raw_crypto_candidate_count"] == 2
