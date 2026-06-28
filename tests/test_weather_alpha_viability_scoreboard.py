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

    assert _row(report, "observation_lock_trade_proxy")["status"] == "no_observed_executable_opportunity"
    assert _row(report, "threshold_latency_trade_proxy")["status"] == "no_observed_executable_opportunity"
    assert report["summary"]["live_should_pause"] is True
    assert report["live_order_path"] is False


def test_alpha_viability_scoreboard_continues_positive_historical_proxy_for_forward_sampling():
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
    assert row["status"] == "historical_proxy_positive_needs_forward_execution_sampling"
    assert "observation_lock_trade_proxy" in report["summary"]["continue_strategy_ids"]
    assert report["historical_trade_proxy_guidance"]["sampler_mode"] == "aggressive"
    assert report["historical_trade_proxy_guidance"]["prioritized_station_codes"] == ["UUWW"]


def test_alpha_viability_scoreboard_adds_eq_dead_no_lock_strategy():
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
        eq_dead_no_sampler_report={"paper_fill_count": 0},
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "eq_dead_no_lock")
    assert row["status"] == "historical_proxy_positive_needs_forward_eq_dead_no_sampling"
    assert "eq_dead_no_lock" in report["summary"]["continue_strategy_ids"]
    assert row["live_eligible"] is False


def test_alpha_viability_scoreboard_uses_eq_dead_no_robustness_status():
    report = build_alpha_viability_scoreboard(
        eq_dead_no_trade_replay_report={
            "summary": {
                "breached_eq_signal_count": 4,
                "deduped_trade_proxy_candidate_count": 2,
                "deduped_trade_proxy_pnl_cents": 12.0,
            }
        },
        eq_dead_no_proxy_robustness_report={
            "summary": {
                "conservative_candidate_count": 2,
                "conservative_proxy_pnl_cents": 9.0,
                "conservative_proxy_pnl_without_top_1": 3.0,
                "conservative_proxy_positive_after_outlier_removal": True,
                "high_confidence_pnl_cents": 9.0,
            }
        },
        eq_dead_no_sampler_report={"paper_fill_count": 0, "breached_eq_count": 0, "active_supported_metar_station_count": 4},
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "eq_dead_no_lock")
    assert row["status"] == "eq_dead_no_historical_proxy_positive_waiting_for_forward_breach"
    assert row["conservative_proxy_candidate_count"] == 2
    assert report["summary"]["eq_dead_no_current_status"] == row["status"]
    assert report["summary"]["active_eq_station_count"] == 4
