from __future__ import annotations

from src.trading.polymarket_alpha.market_family_catalog import build_market_family_catalog


def test_family_catalog_assigns_every_market_or_isolated():
    rows = [
        {"market_slug": "btc-over-100k", "event_slug": "btc-price", "category": "crypto", "title": "BTC over 100k?", "outcomes": ["Yes", "No"], "token_ids": ["a", "b"], "volume": 10},
        {"market_slug": "btc-over-120k", "event_slug": "btc-price", "category": "crypto", "title": "BTC over 120k?", "outcomes": ["Yes", "No"], "token_ids": ["c", "d"], "volume": 15},
        {"market_slug": "one-off", "event_slug": "one-off", "category": "politics", "title": "Will X happen?", "outcomes": ["Yes", "No"], "token_ids": ["e", "f"], "volume": 3},
    ]

    catalog = build_market_family_catalog(rows)

    assert catalog["input_market_count"] == 3
    assert catalog["family_count"] == 2
    family_types = {row["family_type"] for row in catalog["families"]}
    assert "threshold_monotonic_family" in family_types
    assert "isolated_binary" in family_types
    assert catalog["live_order_path"] is False


def test_family_catalog_detects_multi_outcome_family_as_exhaustive():
    catalog = build_market_family_catalog(
        [
            {
                "market_slug": "winner",
                "event_slug": "election-winner",
                "category": "politics",
                "title": "Who wins?",
                "outcomes": ["A", "B", "C"],
                "token_ids": ["a", "b", "c"],
                "volume": 100,
            }
        ]
    )

    family = catalog["families"][0]
    assert family["family_type"] == "mutually_exclusive_outcomes"
    assert family["is_exhaustive"] is True
    assert family["completeness_confidence"] >= 0.9
