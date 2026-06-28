from __future__ import annotations

from src.trading.polymarket_alpha.price_orderbook_join_audit import build_price_orderbook_join_audit_report


def _crypto_market(**overrides):
    row = {
        "active": True,
        "category": "crypto",
        "market_slug": "will-bitcoin-be-above-100000-by-december-31",
        "title": "Will Bitcoin be above $100,000 by December 31?",
        "question": "Will Bitcoin be above $100,000 by December 31?",
        "end_time": "2026-12-31T00:00:00Z",
        "outcomes": ["Yes", "No"],
        "token_ids": ["yes-token", "no-token"],
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "orderbooks": {},
    }
    row.update(overrides)
    return row


def test_price_orderbook_audit_fetches_active_books_and_reports_crypto_side_gaps():
    def fetcher(token_id: str):
        if token_id == "yes-token":
            return {"best_bid": 0.2, "best_ask": 0.25, "spread": 0.05, "ask_depth_usdc_3c": 100}
        return {"best_bid": 0.7, "spread": 0.04}

    report = build_price_orderbook_join_audit_report(
        active_markets=[_crypto_market()],
        snapshot_rows=[
            {"token_id": "yes-token", "data_source": "active_gamma_snapshot_price"},
            {"token_id": "missing-token", "data_source": "polymarket_price_history"},
        ],
        price_history_rows=[{"token_id": "yes-token", "timestamp": "2026-01-01T00:00:00Z", "price": 0.2}],
        fetch_orderbooks=True,
        orderbook_fetcher=fetcher,
    )

    historical = report["historical_price_join"]
    active = report["active_orderbook_join"]
    crypto = report["crypto_specific_diagnostics"]
    assert historical["price_history_found_count"] == 1
    assert historical["token_id_mismatch_count"] == 1
    assert active["orderbook_fetch_attempt_count"] == 2
    assert active["orderbook_fetch_success_count"] == 2
    assert active["best_bid_ask_available_count"] == 1
    assert crypto["parsed_crypto_market_count"] == 1
    assert crypto["crypto_yes_best_ask_available_count"] == 1
    assert crypto["crypto_no_best_ask_available_count"] == 0
    assert crypto["crypto_missing_executable_price_samples"][0]["reason"] == "no_no_ask_depth"
    assert report["live_order_path"] is False


def test_price_orderbook_audit_is_case_insensitive_for_yes_no_outcomes():
    report = build_price_orderbook_join_audit_report(
        active_markets=[
            _crypto_market(
                token_id_by_outcome={"YES": "yes-token", "NO": "no-token"},
                orderbooks={
                    "yes-token": {"best_bid": 0.2, "best_ask": 0.25},
                    "no-token": {"best_bid": 0.7, "best_ask": 0.75},
                },
            )
        ],
        snapshot_rows=[],
        price_history_rows=[],
    )

    crypto = report["crypto_specific_diagnostics"]
    assert crypto["crypto_yes_best_ask_available_count"] == 1
    assert crypto["crypto_no_best_ask_available_count"] == 1
