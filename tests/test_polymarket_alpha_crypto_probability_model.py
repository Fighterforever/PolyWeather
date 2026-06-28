from __future__ import annotations

from src.trading.polymarket_alpha.crypto_probability_model import (
    build_crypto_probability_edge_report,
    lognormal_probability_above,
    parse_crypto_threshold_market,
)


def _market(**overrides):
    row = {
        "active": True,
        "category": "crypto",
        "market_slug": "will-bitcoin-be-above-100000-by-december-31",
        "title": "Will Bitcoin be above $100,000 by December 31?",
        "question": "Will Bitcoin be above $100,000 by December 31?",
        "end_time": "2026-12-31T00:00:00Z",
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "orderbooks": {
            "yes-token": {
                "best_bid": 0.2,
                "best_ask": 0.25,
                "spread": 0.05,
                "ask_depth_usdc_3c": 100,
            }
        },
    }
    row.update(overrides)
    return row


def test_parse_btc_threshold_market():
    parsed = parse_crypto_threshold_market(_market())

    assert parsed is not None
    assert parsed["asset"] == "BTC"
    assert parsed["direction"] == "above"
    assert parsed["threshold"] == 100000.0


def test_lognormal_probability_monotonic_with_threshold():
    low = lognormal_probability_above(spot=50000, threshold=40000, annual_vol=0.5, years=0.5)
    high = lognormal_probability_above(spot=50000, threshold=100000, annual_vol=0.5, years=0.5)

    assert low > high


def test_crypto_candidate_requires_executable_ask():
    report = build_crypto_probability_edge_report(
        active_markets=[_market(orderbooks={})],
        spot_prices={"BTC": 120000},
        generated_at="2026-06-29T00:00:00Z",
        min_edge=0.01,
    )

    assert report["parsed_crypto_market_count"] == 1
    assert report["model_ready_count"] == 1
    assert report["candidate_count"] == 0
    assert report["gap_reasons"][0]["reason"] == "missing_executable_yes_bid_ask"


def test_crypto_gap_when_price_source_missing():
    report = build_crypto_probability_edge_report(
        active_markets=[_market()],
        spot_prices={},
        generated_at="2026-06-29T00:00:00Z",
    )

    assert report["parsed_crypto_market_count"] == 1
    assert report["model_ready_count"] == 0
    assert report["candidate_count"] == 0
    assert report["gap_reasons"][0]["reason"] == "missing_btc_spot_price"
