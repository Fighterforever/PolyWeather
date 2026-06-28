from __future__ import annotations

from src.trading.weather_observation_lock_trade_replay import (
    build_observation_lock_trade_proxy_attribution,
    build_observation_lock_trade_replay_report,
)


def _tradability():
    return {
        "rows": [
            {
                "market_slug": "m",
                "token_id": "yes-token",
                "locked_side": "NO",
                "replay_time": "2026-06-27T09:00:00Z",
                "approximate_price": 0.50,
                "payout": 1.0,
                "station_code": "UUWW",
                "bucket_type": "le",
                "threshold": 20,
                "lock_state": "le_yes_dead_no_locked",
                "time_to_close_bucket": "gt_2h",
            }
        ]
    }


def _eq_tradability():
    payload = _tradability()
    payload["rows"][0].update(
        {
            "bucket_type": "eq",
            "lock_state": "eq_yes_dead_no_locked",
            "threshold": 27,
        }
    )
    return payload


def _closed():
    return {"market_slug": "m", "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"}, "settled_yes_payout": 0.0}


def test_observation_lock_trade_replay_uses_same_no_token_not_yes_inverse():
    report = build_observation_lock_trade_replay_report(
        tradability_report=_tradability(),
        closed_markets=[_closed()],
        trade_tape_rows=[
            {
                "market_slug": "m",
                "token_id": "yes-token",
                "timestamp": "2026-06-27T09:02:00Z",
                "price": 0.01,
                "size": 5,
                "direction_confidence": "reported_by_polymarket_data_api",
            }
        ],
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["summary"]["trade_proxy_candidate_count"] == 0
    assert report["summary"]["same_token_trade_0_5m_count"] == 0
    assert {row["reason"] for row in report["rows"]} >= {"missing_same_token_trade"}


def test_observation_lock_trade_replay_scores_same_token_trade_proxy():
    report = build_observation_lock_trade_replay_report(
        tradability_report=_tradability(),
        closed_markets=[_closed()],
        trade_tape_rows=[
            {
                "market_slug": "m",
                "token_id": "no-token",
                "timestamp": "2026-06-27T09:02:00Z",
                "price": 0.40,
                "size": 5,
                "direction_confidence": "reported_by_polymarket_data_api",
                "source_trade_id": "trade-1",
            }
        ],
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["summary"]["same_token_trade_0_5m_count"] == 1
    assert report["summary"]["trade_proxy_candidate_count"] == 1
    assert report["summary"]["trade_proxy_pnl_cents"] == 60.0
    row = [row for row in report["rows"] if row["approximate_hit_price"] is not None][0]
    assert row["can_count_as_real_pnl"] is False
    assert row["can_count_as_trade_proxy"] is True


def test_trade_proxy_attribution_by_bucket_type():
    report = build_observation_lock_trade_replay_report(
        tradability_report=_eq_tradability(),
        closed_markets=[_closed()],
        trade_tape_rows=[
            {
                "market_slug": "m",
                "token_id": "no-token",
                "timestamp": "2026-06-27T09:00:30Z",
                "price": 0.40,
                "size": 5,
            }
        ],
        generated_at="2026-06-28T00:00:00Z",
    )

    attribution = build_observation_lock_trade_proxy_attribution(replay_report=report)
    eq = next(row for row in attribution["by_bucket_type"] if row["bucket_type"] == "eq")
    assert eq["trade_proxy_candidate_count"] == 1
    assert eq["trade_proxy_pnl_cents"] == 60.0
    assert attribution["eq_dead_no_lock_is_primary_proxy_alpha"] is True


def test_trade_proxy_dedupes_overlapping_windows():
    report = build_observation_lock_trade_replay_report(
        tradability_report=_eq_tradability(),
        closed_markets=[_closed()],
        trade_tape_rows=[
            {"market_slug": "m", "token_id": "no-token", "timestamp": "2026-06-27T09:00:30Z", "price": 0.40, "size": 5},
            {"market_slug": "m", "token_id": "no-token", "timestamp": "2026-06-27T09:02:00Z", "price": 0.30, "size": 5},
        ],
        generated_at="2026-06-28T00:00:00Z",
    )

    attribution = report["attribution"]
    assert attribution["global"]["trade_proxy_candidate_count"] == 2
    assert attribution["global"]["duplicate_window_count"] == 1
    assert attribution["deduplicated"]["deduped_trade_proxy_candidate_count"] == 1
    assert attribution["deduplicated"]["deduped_trade_proxy_pnl_cents"] == 60.0


def test_trade_proxy_identifies_eq_dead_no_lock():
    report = build_observation_lock_trade_replay_report(
        tradability_report=_eq_tradability(),
        closed_markets=[_closed()],
        trade_tape_rows=[
            {"market_slug": "m", "token_id": "no-token", "timestamp": "2026-06-27T09:00:30Z", "price": 0.40, "size": 5}
        ],
        generated_at="2026-06-28T00:00:00Z",
    )

    attribution = report["attribution"]
    lock_state = next(row for row in attribution["by_lock_state"] if row["lock_state"] == "eq_yes_dead_no_locked")
    assert lock_state["trade_proxy_candidate_count"] == 1
    assert attribution["eq_dead_no_lock_is_primary_proxy_alpha"] is True
