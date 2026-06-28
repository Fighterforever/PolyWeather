from __future__ import annotations

from src.trading.polymarket_alpha.probability_dataset import build_probability_dataset


def _market(**overrides):
    row = {
        "market_id": "m1",
        "condition_id": "c1",
        "market_slug": "crypto-up",
        "category": "crypto",
        "tags": ["btc"],
        "event_slug": "btc",
        "outcomes": ["Yes", "No"],
        "token_ids": ["yes-token", "no-token"],
        "outcome_prices": [1.0, 0.0],
        "volume": 1000,
        "liquidity": 500,
        "end_time": "2026-01-02T00:00:00Z",
        "resolved": True,
    }
    row.update(overrides)
    return row


def test_probability_dataset_marks_price_history_as_non_executable_and_no_lookahead():
    report = build_probability_dataset(
        active_markets=[],
        closed_markets=[_market()],
        price_history_rows=[
            {"token_id": "yes-token", "timestamp": "2026-01-01T00:00:00Z", "price": 0.4},
            {"token_id": "yes-token", "timestamp": "2026-01-03T00:00:00Z", "price": 1.0},
        ],
        trade_rows=[{"token_id": "yes-token", "timestamp": "2026-01-01T00:30:00Z"}],
    )

    assert report["manifest"]["snapshot_row_count"] == 1
    assert report["manifest"]["resolved_snapshot_count"] == 1
    assert report["manifest"]["no_lookahead_violation_count"] == 0
    assert report["manifest"]["unique_event_family_count"] == 1
    assert report["manifest"]["mid_price_training_row_count"] == 1
    row = report["rows"][0]
    assert row["decision_snapshot_id"]
    assert row["decision_time"] == "2026-01-01T00:00:00Z"
    assert row["resolved_payout"] == 1.0
    assert row["executable_depth_available"] is False
    assert row["live_order_path"] is False


def test_probability_dataset_uses_active_orderbook_when_available():
    active = _market(
        resolved=False,
        outcome_prices=[0.45, 0.55],
        orderbooks={
            "yes-token": {"best_bid": 0.4, "best_ask": 0.5, "spread": 0.1},
            "no-token": {"best_bid": 0.5, "best_ask": 0.6, "spread": 0.1},
        },
    )
    report = build_probability_dataset(active_markets=[active], closed_markets=[], generated_at="2026-01-01T00:00:00Z")

    assert report["manifest"]["snapshot_row_count"] == 2
    assert all(row["data_source"] == "active_orderbook_snapshot" for row in report["rows"])
    assert all(row["executable_depth_available"] is True for row in report["rows"])


def test_probability_dataset_marks_extreme_rows_outside_mid_training_set():
    report = build_probability_dataset(
        active_markets=[],
        closed_markets=[_market(outcome_prices=[1.0, 0.0])],
        price_history_rows=[
            {"token_id": "yes-token", "timestamp": "2026-01-01T00:00:00Z", "price": 0.99},
        ],
    )

    assert report["manifest"]["extreme_price_row_count"] == 1
    assert report["manifest"]["mid_price_training_row_count"] == 0
    assert report["rows"][0]["is_extreme_price_row"] is True
