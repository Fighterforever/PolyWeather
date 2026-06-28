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
