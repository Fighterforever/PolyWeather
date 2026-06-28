from __future__ import annotations

from src.trading.weather_alpha_viability_scoreboard import build_alpha_viability_scoreboard


def _row(report, strategy_id):
    return next(row for row in report["rows"] if row["strategy_id"] == strategy_id)


def test_alpha_viability_scoreboard_pauses_strategy_without_trade_proxy_or_forward_fill():
    report = build_alpha_viability_scoreboard(
        observation_lock_trade_replay_report={"summary": {"locked_signal_count": 105, "trade_proxy_candidate_count": 0}},
        threshold_latency_trade_replay_report={"summary": {"update_event_count": 10, "trade_proxy_candidate_count": 0}},
        active_sampler_report={"summary": {"paper_fill_count": 0}},
        generated_at="2026-06-28T00:00:00Z",
    )

    assert _row(report, "observation_lock_trade_proxy")["status"] == "collapsed_into_eq_dead_no_or_paused"
    assert _row(report, "threshold_latency")["status"] == "paused_no_candidate"
    assert report["summary"]["live_should_pause"] is True
    assert report["live_order_path"] is False


def test_alpha_viability_scoreboard_collapses_positive_observation_lock_proxy():
    report = build_alpha_viability_scoreboard(
        observation_lock_trade_replay_report={
            "summary": {
                "locked_signal_count": 105,
                "trade_proxy_candidate_count": 2,
                "trade_proxy_pnl_cents": 10.0,
                "by_station": [{"station_code": "UUWW", "trade_proxy_pnl_cents": 10.0}],
                "by_window": [{"window": "0-1m", "trade_proxy_pnl_cents": 10.0}],
            }
        },
        threshold_latency_trade_replay_report={"summary": {"update_event_count": 10, "trade_proxy_candidate_count": 0}},
        active_sampler_report={"summary": {"paper_fill_count": 0}},
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "observation_lock_trade_proxy")
    assert row["status"] == "collapsed_into_eq_dead_no_or_paused"
    assert "observation_lock_trade_proxy" in report["summary"]["pause_strategy_ids"]
    assert report["historical_trade_proxy_guidance"]["sampler_mode"] == "reduced"


def test_alpha_viability_scoreboard_keeps_eq_dead_no_monitoring_when_expanded_not_robust():
    report = build_alpha_viability_scoreboard(
        eq_dead_no_trade_replay_report={
            "summary": {
                "breached_eq_signal_count": 4,
                "deduped_trade_proxy_candidate_count": 2,
                "deduped_trade_proxy_pnl_cents": 12.0,
                "by_station": [{"station_code": "UUWW", "trade_proxy_pnl_cents": 12.0}],
                "by_time_to_close": [{"time_to_close_bucket": "gt_2h", "trade_proxy_pnl_cents": 12.0}],
            }
        },
        eq_dead_no_expanded_robustness_report={
            "status": "eq_dead_no_proxy_not_robust_keep_monitoring_only",
            "sample_count": 1,
            "conservative_proxy_pnl_cents": 47.0,
            "pnl_without_top_1": 0.0,
            "unique_market_count": 1,
        },
        eq_dead_no_sampler_report={"paper_fill_count": 0},
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "eq_dead_no_lock")
    assert row["status"] == "eq_dead_no_proxy_not_robust_keep_monitoring_only"
    assert "eq_dead_no_lock" in report["summary"]["pause_strategy_ids"]
    assert row["live_eligible"] is False


def test_alpha_viability_scoreboard_uses_expanded_eq_dead_no_robustness_status():
    report = build_alpha_viability_scoreboard(
        eq_dead_no_trade_replay_report={
            "summary": {
                "breached_eq_signal_count": 4,
                "deduped_trade_proxy_candidate_count": 2,
                "deduped_trade_proxy_pnl_cents": 12.0,
            }
        },
        eq_dead_no_expanded_robustness_report={
            "status": "eq_dead_no_proxy_robust_enough_for_forward_sampling",
            "sample_count": 10,
            "unique_market_count": 10,
            "conservative_proxy_pnl_cents": 20.0,
            "pnl_without_top_1": 18.0,
        },
        eq_dead_no_sampler_report={
            "paper_fill_count": 0,
            "breached_eq_count": 0,
            "active_supported_metar_station_count": 3,
            "active_supported_official_station_count": 4,
        },
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "eq_dead_no_lock")
    assert row["status"] == "eq_dead_no_proxy_robust_enough_for_forward_sampling"
    assert row["conservative_proxy_candidate_count"] == 10
    assert report["summary"]["eq_dead_no_current_status"] == row["status"]
    assert report["summary"]["active_eq_station_count"] == 4
    assert "eq_dead_no_lock" in report["summary"]["continue_strategy_ids"]


def test_alpha_viability_scoreboard_marks_non_dust_failed_and_current_paths_unproven():
    report = build_alpha_viability_scoreboard(
        non_dust_due_runner_status={
            "alpha_conclusion": "non_dust_threshold_cdf_failed",
            "strict_replay": {"resolved_fill_count": 1, "resolved_pnl_cents": -5.0},
        },
        eq_dead_no_expanded_robustness_report={
            "status": "eq_dead_no_proxy_not_robust_keep_monitoring_only",
            "sample_count": 1,
            "conservative_proxy_pnl_cents": 47.0,
            "pnl_without_top_1": 0.0,
        },
        generated_at="2026-06-28T00:00:00Z",
    )

    assert _row(report, "non_dust_threshold_cdf")["status"] == "failed"
    assert report["summary"]["live_should_pause"] is True
    assert report["summary"]["reason"] == "all_current_weather_alpha_paths_failed_or_unproven"
    assert "dust_tail_near_lock" in report["summary"]["kill_strategy_ids"]
