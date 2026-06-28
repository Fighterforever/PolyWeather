from __future__ import annotations

from src.trading.polymarket_alpha.rule_confusion_scanner import scan_rule_confusion


def test_rule_confusion_detects_time_and_threshold_ambiguity():
    report = scan_rule_confusion(
        [
            {
                "market_id": "m1",
                "market_slug": "ambiguous",
                "category": "macro",
                "title": "Will CPI be above 3 by July 1?",
                "description": "Resolved on official release unless revised later.",
                "liquidity": 1000,
            }
        ]
    )

    assert report["candidate_count"] == 1
    features = report["candidates"][0]["confusion_features"]
    assert "date_time_ambiguity" in features
    assert "threshold_ambiguity" in features
    assert report["live_order_path"] is False


def test_rule_confusion_filters_low_liquidity():
    report = scan_rule_confusion(
        [
            {
                "market_id": "m1",
                "market_slug": "ambiguous",
                "category": "macro",
                "title": "Will CPI be above 3 by July 1?",
                "description": "Resolved on official release unless revised later.",
                "liquidity": 1,
            }
        ],
        min_liquidity=10,
    )

    assert report["candidate_count"] == 0
    assert report["no_candidate_reason_counts"][0]["reason"] == "liquidity_below_min"
