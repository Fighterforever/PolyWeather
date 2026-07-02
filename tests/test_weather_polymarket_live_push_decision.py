from __future__ import annotations

from scripts.weather_polymarket_live_push_decision_report import build_polymarket_weather_live_push_decision


def test_polymarket_live_push_waits_before_non_dust_due():
    report = build_polymarket_weather_live_push_decision(
        non_dust_verdict={
            "due_reached": False,
            "status": "waiting_due",
            "alpha_conclusion": "non_dust_threshold_cdf_waiting_resolution",
        },
        maker_funnel_report={
            "run_count": 1,
            "actual_window_minutes": 0.0,
            "quote_count": 0,
            "conclusion": "maker_shadow_current_snapshot_no_quote",
        },
        station_confusion_report={
            "station_bias_sample_count": 4,
            "min_required_sample_count": 10,
            "station_confusion_status": "research_only_insufficient_bias_samples",
            "candidate_count": 0,
        },
        bucket_family_lp_report={"lp_candidate_count": 0},
        bucket_family_arbitrage_report={"candidate_count": 0},
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["live_push_status"] == "wait_non_dust_due_and_collect_polymarket_only_shadow_evidence"
    assert report["maker_shadow_v2"]["quote_count"] == 0
    assert report["station_confusion"]["station_confusion_status"] == "research_only_insufficient_bias_samples"
    assert report["live_order_path"] is False


def test_polymarket_live_push_pauses_after_non_dust_negative_with_no_other_edge():
    report = build_polymarket_weather_live_push_decision(
        non_dust_verdict={
            "due_reached": True,
            "status": "after_due_artifact_ready",
            "alpha_conclusion": "non_dust_threshold_cdf_failed",
            "strict_replay": {"resolved_fill_count": 1, "resolved_pnl_cents": -4.0},
        },
        maker_funnel_report={
            "quote_count": 0,
            "conclusion": "maker_shadow_rolling_window_insufficient",
        },
        station_confusion_report={
            "station_bias_sample_count": 4,
            "min_required_sample_count": 10,
            "station_confusion_status": "research_only_insufficient_bias_samples",
            "candidate_count": 0,
        },
        bucket_family_lp_report={"lp_candidate_count": 0},
        bucket_family_arbitrage_report={"candidate_count": 0},
        generated_at="2026-06-30T04:00:00Z",
    )

    assert report["live_push_status"] == "pause_polymarket_weather_live_push"
    assert report["non_dust_uuww"]["resolved_pnl_cents"] == -4.0
    assert report["live_order_path"] is False


def test_polymarket_live_push_continues_non_dust_positive_paper_only():
    report = build_polymarket_weather_live_push_decision(
        non_dust_verdict={
            "due_reached": True,
            "status": "after_due_artifact_ready",
            "alpha_conclusion": "non_dust_threshold_cdf_single_positive_needs_more_forward",
            "strict_replay": {"resolved_fill_count": 1, "resolved_pnl_cents": 8.0},
        },
        maker_funnel_report={"quote_count": 0},
        station_confusion_report={"station_bias_sample_count": 0, "min_required_sample_count": 10},
        bucket_family_lp_report={"lp_candidate_count": 0},
        bucket_family_arbitrage_report={"candidate_count": 0},
        generated_at="2026-06-30T04:00:00Z",
    )

    assert report["live_push_status"] == "continue_non_dust_forward_paper_only"
    assert report["live_order_path"] is False
