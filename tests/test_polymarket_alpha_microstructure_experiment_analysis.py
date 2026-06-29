from __future__ import annotations

from src.trading.polymarket_alpha.microstructure_experiment_analysis import build_microstructure_taker_failure_attribution


def test_microstructure_attribution_flags_negative_forward_markout():
    fills = [
        {
            "fill_id": "f1",
            "candidate_id": "f1",
            "policy_id": "taker_follow_imbalance",
            "category": "sports",
            "q_effective": 0.5,
            "spread": 0.04,
            "imbalance": -0.8,
            "source_watch_id": "w1",
        }
    ]
    markouts = [
        {"fill_id": "f1", "candidate_id": "f1", "horizon_seconds": 300, "markout_cents": -1.0, "policy_id": "taker_follow_imbalance"},
        {"fill_id": "f1", "candidate_id": "f1", "horizon_seconds": 900, "markout_cents": -1.5, "policy_id": "taker_follow_imbalance"},
        {"fill_id": "f1", "candidate_id": "f1", "horizon_seconds": 3600, "markout_cents": -2.0, "policy_id": "taker_follow_imbalance"},
    ]
    report = build_microstructure_taker_failure_attribution(
        fills=fills,
        markouts=markouts,
        candidates=fills,
        watch_rows=[{"watch_id": "w1", "category": "sports", "time_to_close": "2026-07-01T00:00:00Z"}],
    )

    assert report["microstructure_taker_status"] == "failed_negative_forward_markout"
    assert report["counts_for_live_gate"] is False
    assert report["live_order_path"] is False
    assert report["answers"]["follow_imbalance_systematically_negative"] is True


def test_microstructure_attribution_reports_bucket_breakdowns():
    report = build_microstructure_taker_failure_attribution(
        fills=[{"fill_id": "f1", "q_effective": 0.95, "spread": 0.12, "imbalance": -0.9}],
        markouts=[{"fill_id": "f1", "horizon_seconds": 300, "markout_cents": -3.0}],
    )

    assert report["by_spread_bucket"][0]["bucket"] == "spread_8_15c"
    assert report["by_price_bucket"][0]["bucket"] == "price_ge_90c"
    assert report["live_order_path"] is False
