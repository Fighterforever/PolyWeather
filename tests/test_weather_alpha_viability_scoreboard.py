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
    assert _row(report, "threshold_latency")["status"] == "paused"
    assert report["summary"]["live_should_pause"] is True
    assert report["scope"] == "polymarket_only"
    assert all(row["platform"] == "polymarket" for row in report["rows"])
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
    assert row["status"] == "monitoring_only_not_robust"
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


def test_alpha_viability_scoreboard_adds_bucket_family_structural_rows():
    report = build_alpha_viability_scoreboard(
        bucket_family_arbitrage_report={
            "family_count": 4,
            "partition_family_count": 2,
            "candidate_count": 2,
            "buy_all_yes_candidate_count": 1,
            "buy_all_no_candidate_count": 0,
            "monotonic_pair_count": 5,
            "monotonic_pair_candidate_count": 1,
            "best_edge_cents": 3.5,
            "basket_paper_fill_count": 2,
        },
        generated_at="2026-06-28T00:00:00Z",
    )

    yes = _row(report, "bucket_family_buy_all_yes")
    no = _row(report, "bucket_family_buy_all_no")
    mono = _row(report, "monotonic_threshold_pair")
    assert yes["status"] == "structural_arbitrage_forward_paper_started"
    assert no["status"] == "low_frequency_monitor_no_current_edge"
    assert mono["status"] == "structural_arbitrage_forward_paper_started"
    assert yes["live_eligible"] is False
    assert "bucket_family_buy_all_yes" in report["summary"]["continue_strategy_ids"]
    assert report["summary"]["bucket_family_structural_arbitrage"]["best_edge_cents"] == 3.5
    assert report["live_order_path"] is False


def test_alpha_viability_scoreboard_marks_historical_structural_edge_as_forward_sampling_only():
    report = build_alpha_viability_scoreboard(
        bucket_family_arbitrage_report={
            "family_count": 3,
            "candidate_count": 0,
            "buy_all_yes_candidate_count": 0,
            "buy_all_no_candidate_count": 0,
            "monotonic_pair_candidate_count": 0,
        },
        bucket_family_historical_replay_report={
            "approximate_edge_candidate_count": 2,
            "executable_depth_available_count": 0,
            "approximate_pnl_cents": 10.0,
        },
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "bucket_family_buy_all_yes")
    assert row["status"] == "historical_structural_edge_needs_forward_sampling"
    assert row["trade_proxy_pnl_cents"] == 10.0
    assert row["live_eligible"] is False


def test_alpha_viability_scoreboard_adds_payoff_matrix_lp_row_and_live_push_verdict():
    report = build_alpha_viability_scoreboard(
        bucket_family_arbitrage_report={"family_count": 4, "candidate_count": 0},
        bucket_family_lp_arbitrage_report={
            "lp_family_count": 4,
            "lp_candidate_count": 1,
            "best_lp_edge_cents": 2.5,
            "lp_near_miss_count": 3,
            "lp_basket_paper_fill_count": 1,
        },
        non_dust_due_runner_status={"status": "waiting_due"},
        generated_at="2026-06-28T00:00:00Z",
    )

    row = _row(report, "bucket_family_payoff_matrix_arbitrage")
    assert row["status"] == "structural_lp_arbitrage_forward_paper_started"
    assert row["forward_paper_fill_count"] == 1
    assert row["live_eligible"] is False
    assert "bucket_family_payoff_matrix_arbitrage" in report["summary"]["continue_strategy_ids"]
    assert report["summary"]["bucket_family_payoff_matrix_arbitrage"]["best_lp_edge_cents"] == 2.5
    assert report["summary"]["live_push_verdict"] == "structural_lp_arbitrage_forward_paper_started"
    assert report["live_order_path"] is False


def test_alpha_viability_scoreboard_waits_non_dust_when_no_structural_arbitrage():
    report = build_alpha_viability_scoreboard(
        bucket_family_arbitrage_report={"family_count": 4, "candidate_count": 0},
        bucket_family_lp_arbitrage_report={"lp_family_count": 4, "lp_candidate_count": 0},
        non_dust_due_runner_status={"status": "waiting_due"},
        generated_at="2026-06-28T00:00:00Z",
    )

    assert _row(report, "bucket_family_payoff_matrix_arbitrage")["status"] == "low_frequency_monitor_no_current_edge"
    assert report["summary"]["live_push_verdict"] == "wait_non_dust_due_only"


def test_alpha_viability_scoreboard_adds_polymarket_only_maker_and_station_rows():
    report = build_alpha_viability_scoreboard(
        maker_shadow_v2_report={
            "quote_count": 12,
            "inferred_fill_count": 3,
            "mean_markout_without_rebate": -0.2,
            "mean_markout_with_rebate": -0.1,
        },
        station_confusion_edge_report={
            "station_bias_sample_count": 5,
            "candidate_count": 1,
            "top_station_biases": [{"station_code": "UUWW", "historical_bias_mean": 2.0}],
        },
        non_dust_due_runner_status={"status": "waiting_due"},
        generated_at="2026-06-28T00:00:00Z",
    )

    maker = _row(report, "maker_shadow_v2")
    station = _row(report, "station_confusion_edge")
    assert maker["status"] == "active_shadow_testing"
    assert maker["forward_paper_fill_count"] == 3
    assert station["status"] == "active_research"
    assert station["signal_count"] == 1
    assert report["summary"]["maker_shadow_v2"]["quote_count"] == 12
    assert report["summary"]["station_confusion_edge"]["top_station_biases"][0]["station_code"] == "UUWW"
    assert report["summary"]["live_push_status"] == "wait_non_dust_due_and_collect_polymarket_only_shadow_evidence"
    assert report["live_order_path"] is False


def test_alpha_viability_scoreboard_continues_maker_research_after_positive_shadow_sample():
    report = build_alpha_viability_scoreboard(
        maker_shadow_v2_report={
            "quote_count": 50,
            "inferred_fill_count": 30,
            "mean_markout_without_rebate": 0.15,
        },
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["summary"]["live_push_status"] == "continue_paper_maker_research"
    assert "maker_shadow_v2" in report["summary"]["continue_research_strategy_ids"]
    assert report["live_order_path"] is False
