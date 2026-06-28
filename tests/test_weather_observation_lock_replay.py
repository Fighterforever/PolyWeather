from __future__ import annotations

from scripts.weather_observation_lock_replay_report import build_observation_lock_replay_report


def test_observation_lock_replay_blocks_without_intraday_timeline():
    orderbook_rows = [
        {
            "market_slug": "highest-temperature-in-moscow-on-june-29-2026-23corhigher",
            "token_id": "yes-token",
            "side": "yes",
            "bucket_type": "ge",
            "threshold": 23.0,
            "target_date": "2026-06-29",
            "settlement_station_code": "UUWW",
            "settlement_source": "metar",
            "recorded_at": "2026-06-29T11:00:00Z",
            "best_bid": 0.60,
            "best_ask": 0.62,
            "spread": 0.02,
            "ask_depth_usdc_3c": 10,
        }
    ]
    closed_rows = [
        {
            "market_slug": "highest-temperature-in-moscow-on-june-29-2026-23corhigher",
            "token_id": "yes-token",
            "official_final_value": 24.0,
            "settlement_spec": {
                "bucket_type": "ge",
                "threshold": 23.0,
                "rounding": "integer_nearest",
            },
        }
    ]

    report = build_observation_lock_replay_report(
        orderbook_rows=orderbook_rows,
        closed_rows=closed_rows,
        observation_rows=[],
        generated_at="2026-06-30T03:05:00Z",
    )

    assert report["replayable_count"] == 0
    assert report["missing_intraday_count"] == 1
    assert report["cannot_replay_lock_without_intraday_timeline"] is True
    assert report["locked_candidate_resolved_pnl_cents"] is None
    assert report["no_lookahead"] is False
