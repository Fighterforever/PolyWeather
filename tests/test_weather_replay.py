from __future__ import annotations

from src.trading.weather_execution_sim import binary_fill_pnl, simulate_taker_buy
from src.trading.weather_replay import replay_taker_candidates


def test_simulate_taker_buy_and_binary_pnl():
    fill = simulate_taker_buy(
        candidate={
            "id": "candidate",
            "generated_at": "2026-06-27T00:00:00Z",
            "market_slug": "m",
            "token_id": "yes-token",
            "side": "yes",
            "p_model": 0.7,
            "p_lcb": 0.64,
        },
        orderbook_snapshot={
            "snapshot_id": "book-1",
            "recorded_at": "2026-06-27T00:00:00Z",
            "ask_ladder": [{"price": 0.40, "size": 2.0}],
            "bid_ladder": [{"price": 0.39, "size": 2.0}],
        },
        size=1.0,
    )
    pnl = binary_fill_pnl(fill, payout=1.0)

    assert fill["paper_only"] is True
    assert fill["source"] == "weather_execution_sim"
    assert fill["available_at"] == "2026-06-27T00:00:00Z"
    assert fill["p_model"] == 0.7
    assert fill["p_lcb"] == 0.64
    assert fill["fully_filled"] is True
    assert fill["entry_price"] == 0.40
    assert pnl["pnl_usdc"] == 0.6
    assert pnl["pnl_cents"] == 60.0


def test_replay_taker_candidates_uses_only_visible_orderbook_snapshots():
    candidates = [
        {
            "id": "candidate-1",
            "generated_at": "2026-06-27T00:05:00Z",
            "market_slug": "m",
            "token_id": "yes-token",
            "side": "yes",
            "p_lcb": 0.70,
        },
        {
            "id": "future-candidate",
            "generated_at": "2026-06-27T02:00:00Z",
            "market_slug": "m",
            "token_id": "future-token",
            "side": "yes",
            "p_lcb": 0.90,
        },
    ]
    orderbooks = [
        {
            "snapshot_id": "early-book",
            "recorded_at": "2026-06-27T00:04:00Z",
            "token_id": "yes-token",
            "ask_ladder": [{"price": 0.40, "size": 2.0}],
            "bid_ladder": [{"price": 0.39, "size": 2.0}],
        },
        {
            "snapshot_id": "future-book",
            "recorded_at": "2026-06-27T01:30:00Z",
            "token_id": "yes-token",
            "ask_ladder": [{"price": 0.10, "size": 2.0}],
            "bid_ladder": [{"price": 0.09, "size": 2.0}],
        },
    ]

    replay = replay_taker_candidates(
        candidates=candidates,
        orderbook_snapshots=orderbooks,
        resolved_outcomes=[{"token_id": "yes-token", "payout": 1.0}],
        replay_time="2026-06-27T01:00:00Z",
        size=1.0,
    )

    assert replay["schema_version"] == "polyweather_weather_replay.v1"
    assert replay["paper_only"] is True
    assert replay["candidate_count"] == 2
    assert replay["fill_count"] == 1
    assert replay["skipped_future_candidate_count"] == 1
    assert replay["fills"][0]["orderbook_snapshot_id"] == "early-book"
    assert replay["fills"][0]["entry_price"] == 0.40
    assert replay["fills"][0]["p_lcb"] == 0.70
    assert replay["resolved_pnl_usdc"] == 0.6
    assert replay["brier_score"] == 0.09
    assert replay["log_loss"] is not None
