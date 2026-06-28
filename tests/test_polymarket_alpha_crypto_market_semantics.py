from __future__ import annotations

from src.trading.polymarket_alpha.crypto_market_semantics import (
    build_crypto_semantics_audit_report,
    classify_crypto_semantics,
)


def _market(**overrides):
    row = {
        "market_slug": "will-bitcoin-reach-70000-by-december-31-2026-from-june-8",
        "title": "Will Bitcoin reach $70,000 by December 31, 2026?",
        "question": "Will Bitcoin reach $70,000 by December 31, 2026?",
        "description": "This market resolves Yes if any Binance 1 minute candle has a final High price equal to or greater than $70,000.",
        "end_time": "2027-01-01T05:00:00Z",
    }
    row.update(overrides)
    return row


def test_reach_by_date_classified_as_touch_barrier():
    assert classify_crypto_semantics(_market()) == "touch_barrier"


def test_above_on_date_classified_as_terminal_or_close():
    assert classify_crypto_semantics(
        _market(
            market_slug="will-bitcoin-be-above-100000-on-december-31",
            title="Will Bitcoin be above $100,000 on December 31?",
            question="Will Bitcoin be above $100,000 on December 31?",
            description="This market resolves based on whether BTC is above $100,000 on December 31.",
        )
    ) in {"terminal_above", "close_above"}


def test_ambiguous_crypto_market_blocked():
    assert classify_crypto_semantics(
        _market(market_slug="bitcoin-market", title="Bitcoin market?", question="Bitcoin market?", description="")
    ) == "ambiguous"


def test_semantics_mismatch_invalidates_fill():
    report = build_crypto_semantics_audit_report(
        fills=[
            {
                "market_slug": "will-bitcoin-reach-70000-by-december-31-2026-from-june-8",
                "token_id": "no-token",
                "side": "NO",
                "probability_semantics": "terminal_above",
                "asset": "BTC",
                "threshold": 70000,
            }
        ],
        active_markets=[_market()],
        generated_at="2026-06-29T00:00:00Z",
    )

    assert report["rows"][0]["semantics_type"] == "touch_barrier"
    assert report["rows"][0]["semantics_match"] is False
    assert report["rows"][0]["action"] == "reprice_with_barrier_model"
    assert report["invalidated_due_semantics_count"] == 1
    assert report["live_order_path"] is False
