from __future__ import annotations

from src.trading.polymarket_alpha.market_discovery import build_market_discovery_report, normalize_market


def _event(**overrides):
    event = {
        "id": "event-1",
        "slug": "fed-decision-july",
        "title": "Fed decision in July",
        "category": "Macro",
        "tags": [{"label": "Rates"}],
        "active": True,
    }
    event.update(overrides)
    return event


def _market(**overrides):
    market = {
        "id": "market-1",
        "conditionId": "cond-1",
        "slug": "fed-cut-in-july",
        "question": "Will the Fed cut rates in July?",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["yes-token","no-token"]',
        "outcomePrices": "[0.4,0.6]",
        "volumeNum": "1000",
        "liquidityNum": "200",
        "active": True,
        "closed": False,
        "endDate": "2026-07-31T00:00:00Z",
    }
    market.update(overrides)
    return market


def test_market_discovery_normalizes_market_and_orderbook():
    row = normalize_market(
        _event(),
        _market(),
        generated_at="2026-06-28T00:00:00Z",
        order_books={
            "yes-token": {"best_bid": 0.39, "best_ask": 0.41, "spread": 0.02, "ask_depth_usdc_3c": 50},
            "no-token": {"best_bid": 0.58, "best_ask": 0.61, "spread": 0.03, "ask_depth_usdc_3c": 60},
        },
    )

    assert row["market_id"] == "market-1"
    assert row["condition_id"] == "cond-1"
    assert row["category"] == "macro"
    assert row["token_id_by_outcome"]["Yes"] == "yes-token"
    assert row["orderbook_available"] is True
    assert row["spread"] == 0.025
    assert row["paper_only"] is True
    assert row["live_order_path"] is False


def test_market_discovery_category_rankings_include_volume_and_counts():
    active = [
        normalize_market(_event(category="Sports"), _market(id="m1", volumeNum="10"), generated_at="2026-06-28T00:00:00Z"),
        normalize_market(_event(category="Sports"), _market(id="m2", slug="m2", volumeNum="20"), generated_at="2026-06-28T00:00:00Z"),
        normalize_market(_event(category="Crypto"), _market(id="m3", slug="m3", volumeNum="100"), generated_at="2026-06-28T00:00:00Z"),
    ]

    report = build_market_discovery_report(active_rows=active, closed_rows=[], generated_at="2026-06-28T00:00:00Z")

    assert report["active_market_count"] == 3
    assert report["closed_market_count"] == 0
    assert report["category_rankings"]["by_active_market_count"][0]["category"] == "sports"
    assert report["category_rankings"]["by_total_volume"][0]["category"] == "crypto"
    assert report["live_order_path"] is False
