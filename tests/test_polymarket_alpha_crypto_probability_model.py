from __future__ import annotations

from src.trading.polymarket_alpha.crypto_probability_model import (
    build_crypto_probability_edge_report,
    build_crypto_touch_sensitivity_report,
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


def test_start_time_unverified_blocks_touch_candidate():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-70000-by-december-31-2026",
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
    )

    assert report["candidate_count"] == 0
    reasons = {row["reason"]: row["count"] for row in report["gap_reasons"]}
    assert reasons["start_time_unverified"] >= 1


def test_verified_high_since_start_no_touch_allows_future_probability():
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
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 65000,
            "max_high_at": "2026-06-20T00:00:00Z",
            "high_since_start_verified": True,
            "barrier_already_touched": False,
            "kline_count": 1000,
            "gap_reason": None,
        },
        min_edge=0.0,
    )

    assert report["touch_barrier_market_count"] == 1
    assert report["high_since_start_verified_count"] == 1
    assert report["barrier_already_touched_count"] == 0
    assert report["verified_not_touched_count"] == 1
    no_row = next(row for row in report["top_10_near_misses"] if row["side"] == "NO")
    assert no_row["high_since_start_verified"] is True
    assert no_row["barrier_already_touched"] is False


def test_touch_near_miss_watch_not_candidate_or_fill():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-85000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $85,000 by December 31, 2026?",
                question="Will Bitcoin reach $85,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $85,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.26, "best_ask": 0.27, "spread": 0.01, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.70, "best_ask": 0.73, "spread": 0.03, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 60000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 65000,
            "max_high_at": "2026-06-20T00:00:00Z",
            "high_since_start_verified": True,
            "barrier_already_touched": False,
            "kline_count": 1000,
            "gap_reason": None,
        },
        min_edge=0.02,
        cost=0.01,
    )

    assert report["candidate_count"] == 0
    assert report["near_miss_watch_count"] >= 1
    watch = report["near_miss_watch"][0]
    assert watch["probability_semantics"] == "touch_barrier"
    assert watch["counts_for_live_gate"] is False
    assert watch["EV_safe"] < 0.02
    assert watch["entry_time"] == "2026-06-29T00:00:00Z"
    assert watch["recorded_at"] == "2026-06-29T00:00:00Z"
    assert watch["orderbook_snapshot_id"]
    assert report["near_miss_orderbook_snapshot_id_null_count"] == 0
    assert report["near_miss_orderbook_snapshots"][0]["source"] == "crypto_touch_near_miss_watch"


def test_near_miss_watch_has_entry_time():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-85000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $85,000 by December 31, 2026?",
                question="Will Bitcoin reach $85,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $85,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.26, "best_ask": 0.27, "spread": 0.01, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.70, "best_ask": 0.73, "spread": 0.03, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 60000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 65000,
            "high_since_start_verified": True,
            "barrier_already_touched": False,
        },
        min_edge=0.02,
        cost=0.01,
    )

    assert report["near_miss_watch_count"] >= 1
    assert all(row.get("entry_time") for row in report["near_miss_watch"])


def test_near_miss_watch_has_orderbook_snapshot_id():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-85000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $85,000 by December 31, 2026?",
                question="Will Bitcoin reach $85,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $85,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {
                        "best_bid": 0.26,
                        "best_ask": 0.27,
                        "spread": 0.01,
                        "ask_depth_usdc_3c": 100,
                        "bids": [{"price": 0.26, "size": 10}],
                        "asks": [{"price": 0.27, "size": 10}],
                    },
                    "no-token": {"best_bid": 0.70, "best_ask": 0.73, "spread": 0.03, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 60000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 65000,
            "high_since_start_verified": True,
            "barrier_already_touched": False,
        },
        min_edge=0.02,
        cost=0.01,
    )

    watch = report["near_miss_watch"][0]
    snapshot_ids = {row["orderbook_snapshot_id"] for row in report["near_miss_orderbook_snapshots"]}
    assert watch["orderbook_snapshot_id"] in snapshot_ids
    assert watch["counts_for_live_gate"] is False


def test_touch_sensitivity_report_is_diagnostic_only():
    report = build_crypto_touch_sensitivity_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-85000-by-december-31-2026-from-june-8",
                title="Will Bitcoin reach $85,000 by December 31, 2026?",
                question="Will Bitcoin reach $85,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $85,000 after market creation.",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.26, "best_ask": 0.27, "spread": 0.01, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.70, "best_ask": 0.73, "spread": 0.03, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 60000},
        annual_vols={"BTC": 0.55},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 65000,
            "high_since_start_verified": True,
            "barrier_already_touched": False,
        },
        min_edge=0.02,
        cost=0.01,
    )

    assert report["live_order_path"] is False
    assert report["rows"]
    assert report["rows"][0]["variant_EV_safe"]
    assert "sensitivity_rank" in report["rows"][0]


def test_proxy_creation_time_blocks_official_candidate_but_keeps_watch():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-85000-by-december-31-2026",
                title="Will Bitcoin reach $85,000 by December 31, 2026?",
                question="Will Bitcoin reach $85,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $85,000 after market creation.",
                earliest_price_history_timestamp="2026-06-09T00:00:00Z",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.78, "best_ask": 0.7965, "spread": 0.0165, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.19, "best_ask": 0.2035, "spread": 0.0135, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 80000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 81000,
            "high_since_start_verified": True,
            "barrier_already_touched": False,
            "kline_count": 1000,
            "gap_reason": None,
        },
        min_edge=0.001,
        cost=0.0,
    )

    assert report["candidate_count"] == 0
    assert report["near_miss_watch_count"] >= 1
    assert report["near_miss_watch"][0]["creation_time_proxy"] is True


def test_proxy_creation_time_rejects_otherwise_valid_candidate():
    report = build_crypto_probability_edge_report(
        active_markets=[
            _market(
                market_slug="will-bitcoin-reach-85000-by-december-31-2026",
                title="Will Bitcoin reach $85,000 by December 31, 2026?",
                question="Will Bitcoin reach $85,000 by December 31, 2026?",
                description="This resolves Yes if any Binance candle High is at least $85,000 after market creation.",
                earliest_price_history_timestamp="2026-06-09T00:00:00Z",
                token_id_by_outcome={"Yes": "yes-token", "No": "no-token"},
                token_ids=["yes-token", "no-token"],
                orderbooks={
                    "yes-token": {"best_bid": 0.2, "best_ask": 0.25, "spread": 0.05, "ask_depth_usdc_3c": 100},
                    "no-token": {"best_bid": 0.7, "best_ask": 0.75, "spread": 0.05, "ask_depth_usdc_3c": 100},
                },
            )
        ],
        spot_prices={"BTC": 80000},
        generated_at="2026-06-29T00:00:00Z",
        high_since_start_fetcher=lambda **kwargs: {
            "max_high_since_start": 81000,
            "high_since_start_verified": True,
            "barrier_already_touched": False,
            "kline_count": 1000,
            "gap_reason": None,
        },
        min_edge=0.001,
        cost=0.0,
    )

    assert report["candidate_count"] == 0
    assert any(row["reason"] == "creation_time_proxy_not_official" for row in report["gap_reasons"])


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
