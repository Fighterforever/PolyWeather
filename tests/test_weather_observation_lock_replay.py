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


def test_observation_lock_replay_scores_paper_fills_against_closed_truth():
    report = build_observation_lock_replay_report(
        orderbook_rows=[],
        closed_rows=[
            {
                "market_slug": "highest-temperature-test",
                "token_id": "yes-token",
                "official_final_value": 24.0,
                "settlement_spec": {"bucket_type": "ge", "threshold": 23.0},
            }
        ],
        observation_rows=[],
        paper_fills=[
            {
                "market_slug": "highest-temperature-test",
                "token_id": "yes-token",
                "locked_side": "YES",
                "q_effective": 0.40,
                "price_bucket": "price_ge_0_03",
                "bucket_type": "ge",
                "station_code": "UUWW",
                "no_lookahead": True,
            }
        ],
        generated_at="2026-06-30T03:05:00Z",
    )

    assert report["fill_count"] == 1
    assert report["resolved_fill_count"] == 1
    assert report["resolved_pnl_cents"] == 60.0
    assert report["brier_score"] == 0.0
    assert report["no_lookahead_pass_count"] == 1
