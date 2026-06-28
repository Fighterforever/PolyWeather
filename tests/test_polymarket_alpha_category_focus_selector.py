from __future__ import annotations

from src.trading.polymarket_alpha.category_focus_selector import build_category_focus_report


def test_category_focus_selects_data_supported_positive_oos_categories():
    report = build_category_focus_report(
        model_report={
            "by_category": [
                {
                    "category": "crypto",
                    "sample_count": 80,
                    "brier_improvement": 0.03,
                    "log_loss_improvement": 0.1,
                    "positive_ev_candidate_rate": 0.05,
                },
                {
                    "category": "weather",
                    "sample_count": 80,
                    "brier_improvement": 0.03,
                    "log_loss_improvement": 0.1,
                    "positive_ev_candidate_rate": 0.05,
                },
            ]
        },
        opportunity_density_report={
            "categories": [
                {"category": "crypto", "active_market_count": 20, "median_depth": 50, "trade_tape_density": 3},
                {"category": "weather", "active_market_count": 20, "median_depth": 50, "trade_tape_density": 3},
            ]
        },
        min_sample_count=50,
    )

    crypto = next(row for row in report["categories"] if row["category"] == "crypto")
    weather = next(row for row in report["categories"] if row["category"] == "weather")
    assert crypto["recommendation"] == "focus_forward_paper"
    assert weather["recommendation"] == "monitor_only"
    assert report["top_categories_for_forward_paper"] == ["crypto"]
    assert report["live_order_path"] is False


def test_category_focus_rejects_negative_oos_categories():
    report = build_category_focus_report(
        model_report={
            "by_category": [
                {
                    "category": "sports",
                    "sample_count": 100,
                    "brier_improvement": -0.01,
                    "log_loss_improvement": -0.02,
                    "positive_ev_candidate_rate": 0.0,
                }
            ]
        },
        opportunity_density_report={"categories": [{"category": "sports", "active_market_count": 10}]},
        min_sample_count=50,
    )

    assert report["categories"][0]["recommendation"] == "reject_for_now"
