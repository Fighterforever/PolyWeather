from __future__ import annotations

from src.trading.polymarket_alpha.crypto_market_semantics import (
    build_crypto_semantics_audit_report,
    classify_crypto_parse_gap,
    classify_crypto_semantics,
    parse_start_time,
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
    assert report["invalidated_fill_count"] == 1
    assert report["valid_fill_count"] == 0
    assert report["live_order_path"] is False


def test_from_june_8_start_before_dec_31_end():
    market = _market()
    start = parse_start_time(
        market,
        generated_at="2026-06-29T00:00:00Z",
        end_time="2027-01-01T05:00:00Z",
    )

    assert start == "2026-06-08T00:00:00Z"
    assert start < market["end_time"]


def test_market_creation_time_preferred_over_slug():
    market = _market(created_at="2026-06-09T12:34:56Z")

    assert parse_start_time(market, generated_at="2026-06-29T00:00:00Z", end_time=market["end_time"]) == "2026-06-09T12:34:56Z"


def test_ambiguous_start_time_blocks_touch_candidate():
    market = _market(market_slug="will-bitcoin-reach-70000-by-december-31-2026")

    assert parse_start_time(market, generated_at="2026-06-29T00:00:00Z", end_time=market["end_time"]) is None


def test_parse_gap_classifies_common_unparseable_reasons():
    assert classify_crypto_parse_gap({"title": "Will Bitcoin be mentioned tomorrow?"}) == "non_threshold_crypto_market"
    assert classify_crypto_parse_gap({"title": "Will Solana reach $500 by December 31?"}) == "unsupported_asset"
    assert classify_crypto_parse_gap({"title": "Will Bitcoin reach a new high by December 31?"}) == "missing_threshold"
