from __future__ import annotations

from src.trading.polymarket_alpha.opportunity_density_scoreboard import build_opportunity_density_scoreboard


def test_opportunity_density_ranks_top_categories_transparently():
    active = [
        {"category": "crypto", "market_slug": "btc", "active": True, "spread": 0.05, "liquidity": 300, "trade_tape": {"trade_count": 50}},
        {"category": "crypto", "market_slug": "eth", "active": True, "spread": 0.06, "liquidity": 200, "trade_tape": {"trade_count": 20}},
        {"category": "weather", "market_slug": "temp", "active": True, "spread": 0.01, "liquidity": 10, "trade_tape": {"trade_count": 0}},
    ]
    families = [
        {"family_id": "c1", "category": "crypto", "market_count": 2},
        {"family_id": "w1", "category": "weather", "market_count": 1},
    ]
    payoff = {
        "candidates": [{"family_id": "c1", "category": "crypto"}],
        "near_misses": [{"family_id": "w1", "category": "weather", "edge_cents": -1}],
    }

    report = build_opportunity_density_scoreboard(
        market_discovery_report={"active_market_count": 3},
        active_markets=active,
        family_catalog={"families": families},
        payoff_arbitrage_report=payoff,
    )

    assert report["active_market_count"] == 3
    assert report["top_categories"][0]["category"] == "crypto"
    assert "score_formula" in report
    assert report["live_order_path"] is False
