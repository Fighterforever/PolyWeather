from __future__ import annotations

from src.trading.polymarket_alpha.alpha_tournament_scoreboard import build_alpha_tournament_scoreboard


def test_tournament_downgrades_negative_crypto_touch_and_promotes_watch_lane():
    report = build_alpha_tournament_scoreboard(
        crypto_touch_validation_report={
            "formal_fills": {"fill_count": 3, "available_markout_count": 3, "mean_1h_markout": -1.6},
            "near_miss_watch": {"watch_count": 10},
            "verdict": {"status": "shadow_only_pending_recalibration"},
        },
        crypto_terminal_report={"candidate_count": 0, "paper_fill_count": 0, "near_miss_count": 2},
        microstructure_report={"candidate_count": 4, "paper_fill_count": 0, "watch_count": 4},
        microstructure_markout_report={},
    )

    assert report["live_order_path"] is False
    assert "crypto_touch_shadow" in report["lanes_downgraded"]
    assert report["top_lane"] == "microstructure"
    micro = [row for row in report["lanes"] if row["lane_id"] == "microstructure"][0]
    assert micro["next_action"] == "collect_forward_markout"


def test_tournament_never_marks_lane_live():
    report = build_alpha_tournament_scoreboard(
        microstructure_report={"candidate_count": 1, "paper_fill_count": 1},
        microstructure_markout_report={"mean_markout_by_horizon": [{"bucket": 3600, "mean_markout_cents": 1.0}], "available_markout_count": 25},
    )

    assert all(row["live_order_path"] is False for row in report["lanes"])
    assert report["live_order_path"] is False


def test_tournament_downgrades_microstructure_when_taker_and_maker_fail():
    report = build_alpha_tournament_scoreboard(
        microstructure_report={"candidate_count": 21, "watch_count": 21},
        microstructure_policy_sweep_report={"policy_candidate_count": 31, "input_watch_count": 21, "taker_fill_count": 10, "maker_quote_count": 21},
        microstructure_markout_report={"available_markout_count": 30, "mean_markout_by_horizon": [{"bucket": 3600, "mean_markout_cents": -3.0}]},
        microstructure_experiment_report={"taker_fill_count": 30, "maker_quote_count": 31, "maker_inferred_fill_count": 0, "mean_1h_markout": -3.0},
        microstructure_attribution_report={"microstructure_taker_status": "failed_negative_forward_markout"},
        microstructure_inverse_report={"decision": "pause_taker_microstructure_entirely"},
        maker_quote_sweep_report={"mode_recommendation": "maker_adverse_selection"},
        crypto_terminal_report={"near_miss_count": 2},
    )

    lanes = {row["lane_id"]: row for row in report["lanes"]}
    assert lanes["microstructure_taker"]["status"] == "failed_negative_forward_markout"
    assert lanes["microstructure_maker"]["status"] == "maker_adverse_selection"
    assert lanes["maker_shadow"]["status"] == "maker_adverse_selection"
    assert lanes["microstructure_policy_sweep"]["priority"] <= 8
    assert report["top_lane"] != "microstructure_policy_sweep"
    assert "maker_shadow" in report["lanes_paused"]


def test_tournament_weather_lp_waits_for_reward_metadata():
    report = build_alpha_tournament_scoreboard(
        weather_lp_experiment_report={
            "reward_market_count": 0,
            "paper_quote_count": 0,
            "inferred_fill_count": 0,
            "recommendation": "reward_metadata_pipeline_broken_or_no_rewards",
            "live_order_path": False,
        },
        payoff_arbitrage_report={"candidate_count": 1, "near_miss_count": 2},
    )

    lanes = {row["lane_id"]: row for row in report["lanes"]}
    assert lanes["weather_lp_reward"]["status"] == "reward_metadata_pipeline_broken_or_no_rewards"
    assert lanes["weather_lp_reward"]["next_action"] == "collect_reward_metadata"
    assert lanes["weather_lp_reward"]["live_order_path"] is False
    assert report["top_lane"] != "weather_lp_reward"


def test_tournament_weather_lp_needs_fifty_quotes_before_top_lane():
    report = build_alpha_tournament_scoreboard(
        weather_lp_experiment_report={
            "reward_metadata_available_count": 89,
            "reward_qualified_quote_count": 20,
            "paper_quote_count": 20,
            "recommendation": "continue_weather_lp_paper",
            "live_order_path": False,
        },
        payoff_arbitrage_report={"candidate_count": 1, "near_miss_count": 2},
    )

    lanes = {row["lane_id"]: row for row in report["lanes"]}
    assert lanes["weather_lp_reward"]["status"] == "continue_weather_lp_paper_insufficient_quotes"
    assert lanes["weather_lp_reward"]["priority"] < 80
    assert report["top_lane"] != "weather_lp_reward"
