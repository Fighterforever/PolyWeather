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
