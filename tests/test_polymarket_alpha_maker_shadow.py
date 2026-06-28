from __future__ import annotations

from src.trading.polymarket_alpha.maker_shadow import build_maker_shadow_report


def _market(**overrides):
    row = {
        "category": "crypto",
        "market_slug": "btc-over-100k",
        "event_slug": "btc",
        "active": True,
        "orderbooks": {
            "yes-token": {
                "best_bid": 0.4,
                "best_ask": 0.5,
                "spread": 0.1,
                "ask_depth_usdc_3c": 100,
            }
        },
    }
    row.update(overrides)
    return row


def test_polymarket_maker_shadow_quotes_inside_spread():
    report = build_maker_shadow_report(
        active_markets=[_market()],
        opportunity_scoreboard={"top_categories": [{"category": "crypto"}]},
        min_spread=0.03,
        min_depth=10,
        maker_margin=0.01,
    )

    assert report["quote_count"] == 2
    assert report["quotes"][0]["current_best_bid"] < report["quotes"][0]["quote_price"] < report["quotes"][0]["current_best_ask"]
    assert report["live_order_path"] is False


def test_polymarket_maker_shadow_excludes_dust_and_low_spread():
    report = build_maker_shadow_report(
        active_markets=[
            _market(orderbooks={"dust": {"best_bid": 0.001, "best_ask": 0.004, "spread": 0.003, "ask_depth_usdc_3c": 100}}),
            _market(market_slug="tight", orderbooks={"yes": {"best_bid": 0.49, "best_ask": 0.5, "spread": 0.01, "ask_depth_usdc_3c": 100}}),
        ],
        opportunity_scoreboard={"top_categories": [{"category": "crypto"}]},
    )

    assert report["quote_count"] == 0
    assert report["blocker_counts"]["dust_price"] == 1
    assert report["blocker_counts"]["spread_below_min"] == 1


def test_polymarket_maker_shadow_not_live_eligible():
    report = build_maker_shadow_report(
        active_markets=[_market()],
        opportunity_scoreboard={"top_categories": [{"category": "crypto"}]},
    )

    assert report["counts_for_live_gate"] is False
    assert all(row["live_order_path"] is False for row in report["quotes"])
