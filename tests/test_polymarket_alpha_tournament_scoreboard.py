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
