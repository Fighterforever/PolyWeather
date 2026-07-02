from __future__ import annotations

from src.trading.weather_threshold_latency_trade_replay import build_threshold_latency_trade_replay_report


def _closed(bucket_type: str = "ge"):
    return {
        "market_slug": "m",
        "market_id": "market-1",
        "market_family": "temperature",
        "settlement_source": "metar",
        "station_code": "UUWW",
        "target_date": "2026-06-27",
        "bucket_type": bucket_type,
        "threshold": 20.0,
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "settled_yes_payout": 1.0 if bucket_type == "ge" else 0.0,
        "settlement_spec": {"market_close_time": "2026-06-27T12:00:00Z"},
    }


def _obs(temp: float, available_at: str):
    return {
        "station_code": "UUWW",
        "target_date_local": "2026-06-27",
        "settlement_source": "metar",
        "temperature_c": temp,
        "available_at": available_at,
    }


def test_threshold_latency_trade_replay_scores_trade_after_crossing_without_lookahead():
    report = build_threshold_latency_trade_replay_report(
        closed_markets=[_closed("ge")],
        observations=[
            _obs(19, "2026-06-27T09:00:00Z"),
            _obs(20, "2026-06-27T09:05:00Z"),
        ],
        trade_tape_rows=[
            {"market_slug": "m", "token_id": "yes-token", "timestamp": "2026-06-27T09:04:00Z", "price": 0.1, "size": 1},
            {"market_slug": "m", "token_id": "yes-token", "timestamp": "2026-06-27T09:05:30Z", "price": 0.40, "size": 1},
        ],
        generated_at="2026-06-28T00:00:00Z",
        near_margin_c=0.0,
    )

    assert report["summary"]["crossing_event_count"] == 1
    assert report["summary"]["trade_after_event_count"] == 1
    assert report["summary"]["trade_proxy_candidate_count"] == 4
    assert report["summary"]["trade_proxy_pnl_cents"] == 240.0
    assert all(row["can_count_as_real_pnl"] is False for row in report["rows"])


def test_threshold_latency_trade_replay_reports_missing_trade_when_crossing_has_no_print():
    report = build_threshold_latency_trade_replay_report(
        closed_markets=[_closed("ge")],
        observations=[
            _obs(19, "2026-06-27T09:00:00Z"),
            _obs(20, "2026-06-27T09:05:00Z"),
        ],
        trade_tape_rows=[],
        generated_at="2026-06-28T00:00:00Z",
        near_margin_c=0.0,
    )

    assert report["summary"]["crossing_event_count"] == 1
    assert report["summary"]["trade_proxy_candidate_count"] == 0
    assert report["summary"]["missing_trade_count"] == 4
