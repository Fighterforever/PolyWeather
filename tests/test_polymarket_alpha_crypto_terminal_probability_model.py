from __future__ import annotations

from src.trading.polymarket_alpha.crypto_terminal_probability_model import (
    build_crypto_terminal_edge_report,
    parse_crypto_terminal_market,
)


def _market(question: str, **overrides):
    row = {
        "active": True,
        "category": "crypto",
        "market_slug": "btc-terminal",
        "question": question,
        "title": question,
        "end_time": "2026-12-31T23:59:00Z",
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "orderbooks": {
            "yes-token": {"best_bid": 0.2, "best_ask": 0.25, "spread": 0.05, "ask_depth_usdc_3c": 100},
            "no-token": {"best_bid": 0.7, "best_ask": 0.75, "spread": 0.05, "ask_depth_usdc_3c": 100},
        },
    }
    row.update(overrides)
    return row


def test_terminal_above_parse():
    parsed = parse_crypto_terminal_market(_market("Will BTC be above $100,000 on December 31, 2026?"))

    assert parsed["parsed"] is True
    assert parsed["asset"] == "BTC"
    assert parsed["threshold"] == 100000
    assert parsed["semantics_type"] == "terminal_above"


def test_reach_by_date_excluded_from_terminal_lane():
    parsed = parse_crypto_terminal_market(_market("Will Bitcoin reach $100,000 by December 31, 2026?"))

    assert parsed["parsed"] is False
    assert parsed["gap_reason"] == "touch_barrier_excluded"


def test_terminal_no_side_ev_uses_no_lcb():
    market = _market("Will BTC be above $150,000 on December 31, 2026?")
    report = build_crypto_terminal_edge_report(
        active_markets=[market],
        spot_prices={"BTC": 100000},
        annual_vols={"BTC": 0.1},
        generated_at="2026-06-30T00:00:00Z",
        min_edge=0.01,
        cost=0.0,
        model_haircut=0.01,
    )

    no_rows = [row for row in report["watch_rows"] if row["side"] == "NO"]
    assert no_rows
    no_row = no_rows[0]
    assert no_row["EV_safe"] == round(no_row["p_no_lcb"] - no_row["best_ask"], 8)


def test_terminal_candidate_requires_executable_ask():
    market = _market(
        "Will BTC be above $100,000 on December 31, 2026?",
        orderbooks={"yes-token": {"best_bid": 0.2, "spread": 0.05, "ask_depth_usdc_3c": 100}},
    )
    report = build_crypto_terminal_edge_report(
        active_markets=[market],
        spot_prices={"BTC": 100000},
        generated_at="2026-06-30T00:00:00Z",
    )

    assert report["candidate_count"] == 0
    assert any(row["reason"] == "yes_no_ask_depth" or row["reason"] == "no_orderbook_missing" for row in report["blocker_counts"])
    assert report["live_order_path"] is False
