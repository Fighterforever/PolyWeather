from __future__ import annotations

from src.trading.weather_eq_dead_no_trade_replay import (
    build_eq_dead_no_proxy_robustness_report,
    build_eq_dead_no_trade_replay_report,
)


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


def _proxy_row(
    *,
    market_slug: str = "m",
    token_id: str = "no-token",
    replay_time: str = "2026-06-27T09:00:00Z",
    window: str = "0-1m",
    pnl: float = 20.0,
    price: float = 0.8,
    confidence: str | None = "reported_by_polymarket_data_api",
    trade_id: str | None = "trade-1",
):
    return {
        "market_slug": market_slug,
        "token_id": token_id,
        "replay_time": replay_time,
        "station_code": "UUWW",
        "target_date": "2026-06-27",
        "bucket_type": "eq",
        "lock_state": "eq_yes_dead_no_locked",
        "locked_side": "NO",
        "window": window,
        "approximate_hit_price": price,
        "trade_replay_pnl_cents": pnl,
        "payout": 1.0,
        "size_at_or_better": 4.0,
        "trade_count": 2,
        "first_trade_at": "2026-06-27T09:00:20Z",
        "trade_direction_confidence": confidence,
        "trade_source_trade_id": trade_id,
    }


def test_eq_dead_no_robustness_splits_unknown_direction():
    report = build_eq_dead_no_proxy_robustness_report(
        observation_lock_trade_rows=[
            _proxy_row(market_slug="a", token_id="a-no", pnl=10.0, confidence="reported_by_polymarket_data_api"),
            _proxy_row(market_slug="b", token_id="b-no", pnl=11.0, confidence=None),
        ]
    )

    split = report["direction_confidence_split"]
    assert split["high_confidence_candidate_count"] == 1
    assert split["high_confidence_pnl_cents"] == 10.0
    assert split["null_direction_candidate_count"] == 1
    assert split["null_direction_pnl_cents"] == 11.0
    assert split["unknown_direction_excluded_pnl_cents"] == 11.0


def test_eq_dead_no_robustness_removes_top_outlier():
    report = build_eq_dead_no_proxy_robustness_report(
        observation_lock_trade_rows=[
            _proxy_row(market_slug="outlier", token_id="outlier-no", pnl=47.0),
            _proxy_row(market_slug="small", token_id="small-no", replay_time="2026-06-27T09:01:00Z", pnl=1.0),
        ]
    )

    outliers = report["outlier_analysis"]
    assert outliers["top_1_contribution_cents"] == 47.0
    assert outliers["pnl_without_top_1"] == 1.0
    assert outliers["top_1_contribution_pct"] > 90.0


def test_eq_dead_no_conservative_proxy_requires_trade_id_and_direction():
    report = build_eq_dead_no_proxy_robustness_report(
        observation_lock_trade_rows=[
            _proxy_row(market_slug="good", token_id="good-no", pnl=12.0, trade_id="trade-good"),
            _proxy_row(market_slug="no-id", token_id="no-id-no", pnl=13.0, trade_id=None),
            _proxy_row(market_slug="unknown", token_id="unknown-no", pnl=14.0, confidence="unknown"),
        ]
    )

    conservative = report["strict_conservative_proxy"]
    assert conservative["conservative_candidate_count"] == 1
    assert conservative["conservative_proxy_pnl_cents"] == 12.0
    assert conservative["can_count_as_real_pnl"] is False
    assert conservative["can_count_as_trade_proxy"] is True


def test_eq_dead_no_conservative_proxy_dedupes_windows():
    report = build_eq_dead_no_proxy_robustness_report(
        observation_lock_trade_rows=[
            _proxy_row(window="0-1m", pnl=10.0, trade_id="early"),
            _proxy_row(window="1-3m", pnl=20.0, trade_id="late"),
        ]
    )

    conservative = report["strict_conservative_proxy"]
    assert conservative["conservative_candidate_count"] == 1
    assert conservative["conservative_proxy_pnl_cents"] == 10.0
