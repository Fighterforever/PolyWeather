from __future__ import annotations

from src.trading.polymarket_historical_price_backfill import normalize_price_history_rows


def test_price_history_is_not_executable_depth_by_default():
    rows = normalize_price_history_rows(
        {"history": [{"t": 1782000000, "p": 0.42}]},
        token_id="yes-token",
        market_slug="m",
    )

    assert len(rows) == 1
    assert rows[0]["price"] == 0.42
    assert rows[0]["executable_depth_available"] is False
    assert rows[0]["can_compute_taker_pnl"] is False
    assert rows[0]["paper_only"] is True
