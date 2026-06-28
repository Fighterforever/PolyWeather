from __future__ import annotations

from src.trading.weather_eq_dead_no_trade_replay import build_eq_dead_no_trade_replay_report


def test_eq_dead_no_trade_replay_filters_and_dedupes_eq_dead_no_rows():
    report = build_eq_dead_no_trade_replay_report(
        observation_lock_trade_rows=[
            {
                "market_slug": "m",
                "token_id": "no-token",
                "replay_time": "2026-06-27T09:00:00Z",
                "station_code": "UUWW",
                "time_to_close_bucket": "gt_2h",
                "bucket_type": "eq",
                "lock_state": "eq_yes_dead_no_locked",
                "locked_side": "NO",
                "window": "0-1m",
                "approximate_hit_price": 0.4,
                "trade_replay_pnl_cents": 60.0,
            },
            {
                "market_slug": "m",
                "token_id": "no-token",
                "replay_time": "2026-06-27T09:00:00Z",
                "station_code": "UUWW",
                "time_to_close_bucket": "gt_2h",
                "bucket_type": "eq",
                "lock_state": "eq_yes_dead_no_locked",
                "locked_side": "NO",
                "window": "1-3m",
                "approximate_hit_price": 0.3,
                "trade_replay_pnl_cents": 70.0,
            },
            {
                "market_slug": "m2",
                "token_id": "no-token-2",
                "replay_time": "2026-06-27T09:00:00Z",
                "bucket_type": "le",
                "lock_state": "le_yes_dead_no_locked",
                "locked_side": "NO",
                "window": "0-1m",
                "approximate_hit_price": 0.4,
                "trade_replay_pnl_cents": 60.0,
            },
        ]
    )

    summary = report["summary"]
    assert summary["trade_proxy_candidate_count"] == 2
    assert summary["deduped_trade_proxy_candidate_count"] == 1
    assert summary["deduped_trade_proxy_pnl_cents"] == 60.0
    assert report["rows"][0]["strategy_id"] == "eq_dead_no_lock"
