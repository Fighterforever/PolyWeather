from __future__ import annotations

from src.trading.polymarket_trade_tape_backfill import build_trade_tape_backfill_report


def _signal(side: str = "NO"):
    return {
        "market_slug": "highest-temperature-in-test-on-june-27-2026-20c",
        "token_id": "yes-token",
        "locked_side": side,
        "replay_time": "2026-06-27T09:00:00Z",
    }


def _closed():
    return {
        "market_slug": "highest-temperature-in-test-on-june-27-2026-20c",
        "market_id": "market-1",
        "conditionId": "condition-1",
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
    }


def test_trade_tape_backfill_normalizes_public_trade_rows_without_price_history():
    def fetcher(record, condition_id):
        return (
            [
                {
                    "asset": "no-token",
                    "outcome": "No",
                    "side": "BUY",
                    "price": 0.45,
                    "size": 10,
                    "timestamp": "2026-06-27T09:00:00Z",
                    "transactionHash": "0xtrade",
                    "slug": record["market_slug"],
                }
            ],
            condition_id,
            [],
        )

    report = build_trade_tape_backfill_report(
        locked_signals=[_signal()],
        closed_markets=[_closed()],
        trade_fetcher=fetcher,
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["manifest"]["attempted_locked_signal_count"] == 1
    assert report["manifest"]["token_count"] == 1
    assert report["manifest"]["trade_count"] == 1
    assert report["manifest"]["no_price_history_used_as_trades"] is True
    trade = report["trades"][0]
    assert trade["token_id"] == "no-token"
    assert trade["timestamp"] == "2026-06-27T09:00:00Z"
    assert trade["executable_proxy"] is True
    assert trade["exact_orderbook_depth_available"] is False
    assert trade["counts_for_live_gate"] is False


def test_trade_tape_backfill_records_gap_when_no_public_trades():
    def fetcher(record, condition_id):
        return [], condition_id, []

    report = build_trade_tape_backfill_report(
        locked_signals=[_signal()],
        closed_markets=[_closed()],
        trade_fetcher=fetcher,
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["manifest"]["trade_count"] == 0
    assert report["manifest"]["gap_counts"] == [{"gap_reason": "no_trades_in_window", "count": 1}]
