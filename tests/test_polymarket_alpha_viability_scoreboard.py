from __future__ import annotations

from src.trading.polymarket_alpha.alpha_viability_scoreboard import build_alpha_viability_scoreboard


def test_polymarket_alpha_viability_keeps_weather_as_research_module():
    report = build_alpha_viability_scoreboard(
        opportunity_density_report={"top_categories": [{"category": "crypto"}, {"category": "sports"}]},
        payoff_arbitrage_report={"structural_candidate_count": 0, "candidates": []},
        maker_shadow_report={"quote_count": 0, "inferred_fill_count": 0},
        rule_confusion_report={"candidate_count": 3},
        weather_decision_report={"live_push_status": "wait_non_dust_due_and_collect_polymarket_only_shadow_evidence"},
    )

    assert report["scope"] == "polymarket_only"
    assert report["weather_module_status"] == "research_monitoring_only_until_non_dust_due"
    assert report["summary"]["top_opportunity_category"] == "crypto"
    assert report["summary"]["rule_confusion_candidate_count"] == 3
    assert report["summary"]["live_order_path"] is False
    assert all(row["live_eligible"] is False for row in report["rows"])


def test_polymarket_alpha_viability_prioritizes_structural_candidates():
    report = build_alpha_viability_scoreboard(
        opportunity_density_report={"top_categories": [{"category": "macro"}]},
        payoff_arbitrage_report={"structural_candidate_count": 1, "candidates": [{"family_id": "f1", "category": "macro"}]},
        maker_shadow_report={"quote_count": 2, "inferred_fill_count": 0},
        rule_confusion_report={"candidate_count": 0},
    )

    assert report["final_next_focus"] == "payoff_matrix_arbitrage_candidates_paper_review"
    assert report["live_order_path"] is False


def test_polymarket_alpha_viability_tracks_probability_edge_factory_status():
    report = build_alpha_viability_scoreboard(
        opportunity_density_report={"top_categories": [{"category": "crypto"}]},
        payoff_arbitrage_report={"structural_candidate_count": 0, "candidates": []},
        maker_shadow_report={"quote_count": 0, "inferred_fill_count": 0},
        rule_confusion_report={"candidate_count": 0},
        probability_edge_model_report={
            "candidate_count": 5,
            "by_category": [{"category": "crypto", "brier_improvement": 0.01, "log_loss_improvement": 0.02}],
        },
        active_probability_edge_report={"candidate_count": 0, "paper_fill_count": 0},
        category_focus_report={"top_focus_categories": [{"category": "crypto", "recommendation": "focus_forward_paper"}]},
        probability_markout_report={"markout_count": 0, "mean_markout": None},
        probability_resolved_audit_report={"resolved_fill_count": 0, "resolved_pnl_cents": None},
        maker_shadow_focus_report={"quote_count": 1, "inferred_fill_count": 0},
    )

    assert report["summary"]["live_push_status"] == "wait_for_forward_candidates"
    assert report["summary"]["top_focus_categories"] == ["crypto"]
    assert report["summary"]["maker_shadow_focus_quote_count"] == 1
    assert report["live_order_path"] is False


def test_polymarket_alpha_viability_collects_markouts_when_active_crypto_fills_exist():
    report = build_alpha_viability_scoreboard(
        opportunity_density_report={"top_categories": [{"category": "crypto"}]},
        payoff_arbitrage_report={"structural_candidate_count": 0, "candidates": []},
        maker_shadow_report={"quote_count": 0, "inferred_fill_count": 0},
        rule_confusion_report={"candidate_count": 0},
        probability_edge_model_report={
            "candidate_count": 0,
            "by_category": [],
            "oos_blocker_reason": "need_at_least_two_event_families_for_leave_family_out",
        },
        active_probability_edge_report={"candidate_count": 9, "paper_fill_count": 9},
        probability_markout_report={"markout_count": 0, "mean_markout": None},
        probability_resolved_audit_report={"resolved_fill_count": 0, "resolved_pnl_cents": None},
    )

    assert report["summary"]["forward_paper_fill_count"] == 9
    assert report["summary"]["live_push_status"] == "collect_forward_markouts"
    assert report["summary"]["final_next_focus"] == "probability_edge_forward_paper_markout"
    assert report["live_order_path"] is False


def test_polymarket_alpha_viability_adds_crypto_touch_status():
    report = build_alpha_viability_scoreboard(
        opportunity_density_report={"top_categories": [{"category": "crypto"}]},
        payoff_arbitrage_report={"structural_candidate_count": 0, "candidates": []},
        maker_shadow_report={"quote_count": 0, "inferred_fill_count": 0},
        rule_confusion_report={"candidate_count": 0},
        active_probability_edge_report={"candidate_count": 3, "paper_fill_count": 3},
        probability_markout_report={"markout_count": 12, "mean_markout": -0.01},
        crypto_touch_validation_report={
            "formal_fills": {"fill_count": 3, "available_markout_count": 12, "mean_5m_markout": -1.5},
            "near_miss_watch": {"mean_5m_markout": -1.0},
            "verdict": {"do_not_lower_threshold": True},
        },
    )

    row = next(row for row in report["rows"] if row["strategy_id"] == "crypto_touch_barrier")
    assert row["paper_fill_count"] == 3
    assert row["main_blocker"] == "crypto_touch_forward_markout_negative_reduce_priority"
    assert report["summary"]["crypto_touch_do_not_lower_threshold"] is True
    assert report["live_order_path"] is False
