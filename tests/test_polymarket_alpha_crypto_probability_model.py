from __future__ import annotations

from src.trading.polymarket_alpha.crypto_probability_model import (
    build_crypto_probability_edge_report,
    first_passage_probability_upper,
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


def test_parse_crypto_threshold_rejects_date_only_numbers():
    parsed = parse_crypto_threshold_market(
        _market(
            market_slug="will-a-new-country-buy-bitcoin-by-june-30-2026",
            title="Will a new country buy Bitcoin by June 30, 2026?",
            question="Will a new country buy Bitcoin by June 30, 2026?",
        )
    )

    assert parsed is None


def test_lognormal_probability_monotonic_with_threshold():
    low = lognormal_probability_above(spot=50000, threshold=40000, annual_vol=0.5, years=0.5)
    high = lognormal_probability_above(spot=50000, threshold=100000, annual_vol=0.5, years=0.5)

    assert low > high


def test_touch_probability_greater_than_terminal_probability():
    terminal = lognormal_probability_above(spot=50000, threshold=70000, annual_vol=0.6, years=0.5)
    touch = first_passage_probability_upper(spot=50000, threshold=70000, annual_vol=0.6, years=0.5)

    assert touch > terminal


def test_no_probability_zero_if_barrier_already_touched():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-70000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $70,000 by December 31, 2026?",
                question="Will Bitcoin reach $70,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $70,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.98, "best_ask": 0.99, "spread": 0.01, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.01, "best_ask": 0.02, "spread": 0.01, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 80000},
        generated_at="2026-06-29T00:00:00Z",
        min_edge=0.001,
    )

    assert report["model_ready_count"] == 1
    assert report["candidate_count"] == 0
    no_row = next(row for row in report["top_10_near_misses"] if row["side"] == "NO")
    assert no_row["p_no_lcb"] == 0.0


def test_no_fill_if_high_since_start_unverified_for_touch_market():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-70000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $70,000 by December 31, 2026?",
                question="Will Bitcoin reach $70,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $70,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.4, "best_ask": 0.45, "spread": 0.05, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.5, "best_ask": 0.55, "spread": 0.05, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 60000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda asset, start, end: None,
    )

    assert report["candidate_count"] == 0
    reasons = {row["reason"]: row["count"] for row in report["gap_reasons"]}
    assert reasons["high_since_start_unverified"] >= 1


def test_no_side_ev_uses_touch_probability():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-70000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $70,000 by December 31, 2026?",
                question="Will Bitcoin reach $70,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $70,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.35, "best_ask": 0.4, "spread": 0.05, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.55, "best_ask": 0.6, "spread": 0.05, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 60000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda asset, start, end: 65000,
        min_edge=0.0,
    )

    no_row = next(row for row in report["top_10_near_misses"] if row["side"] == "NO")
    assert no_row["probability_semantics"] == "touch_barrier"
    assert no_row["p_trade_lcb"] == no_row["p_no_lcb"]


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
    reasons = {row["reason"] for row in report["gap_reasons"]}
    assert "yes_token_not_found_or_orderbook_missing" in reasons
    assert report["executable_price_available_count"] == 0
    assert report["top_10_near_misses"][0]["side"] in {"YES", "NO"}


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


def test_crypto_report_has_executable_near_miss_when_ev_is_low():
    report = build_crypto_probability_edge_report(
        active_markets=[_market()],
        spot_prices={"BTC": 50000},
        generated_at="2026-06-29T00:00:00Z",
        min_edge=0.50,
    )

    assert report["parsed_crypto_market_count"] == 1
    assert report["model_ready_count"] == 1
    assert report["executable_price_available_count"] == 1
    assert report["candidate_count"] == 0
    assert report["top_10_near_misses"][0]["best_ask"] is not None
    assert report["top_10_near_misses"][0]["blocker"] in {"ev_below_min", "no_ask_depth", "spread_too_wide"}
