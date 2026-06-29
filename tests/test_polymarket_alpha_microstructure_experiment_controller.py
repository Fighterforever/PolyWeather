from __future__ import annotations

from scripts.polymarket_alpha_microstructure_experiment_controller import build_microstructure_experiment_report


def test_microstructure_controller_promotes_positive_taker_after_sample_threshold():
    report = build_microstructure_experiment_report(
        watch_rows=[{"watch_id": "w"}],
        policy_sweep_report={"policy_candidate_count": 30, "by_policy": [{"policy_id": "taker_follow_imbalance", "candidate_count": 30}]},
        taker_fills=[{"fill_id": str(index), "entry_time": "2026-06-30T00:00:00Z"} for index in range(30)],
        maker_quotes=[],
        markout_report={"available_markout_count": 30, "mean_markout_by_horizon": [{"bucket": "3600", "mean_markout_cents": 1.2}]},
    )

    assert report["recommendation"] == "promote_policy_to_focused_paper"
    assert report["top_policy"] == "taker_follow_imbalance"
    assert report["live_order_path"] is False


def test_microstructure_controller_keeps_maker_shadow_when_no_taker_fills():
    report = build_microstructure_experiment_report(
        watch_rows=[{"watch_id": "w"}],
        policy_sweep_report={"policy_candidate_count": 30},
        taker_fills=[],
        maker_quotes=[{"quote_id": str(index)} for index in range(30)],
        markout_report={},
    )

    assert report["recommendation"] == "maker_quotes_not_getting_filled"
    assert report["maker_quote_count"] == 30
    assert report["maker_inferred_fill_count"] == 0


def test_microstructure_controller_pauses_negative_taker_after_sample_threshold():
    report = build_microstructure_experiment_report(
        watch_rows=[],
        policy_sweep_report={"policy_candidate_count": 30},
        taker_fills=[{"fill_id": str(index)} for index in range(30)],
        maker_quotes=[],
        markout_report={"available_markout_count": 30, "mean_markout_by_horizon": [{"bucket": "3600", "mean_markout_cents": -0.5}]},
    )

    assert report["recommendation"] == "pause_taker_microstructure"
    assert report["live_order_path"] is False
