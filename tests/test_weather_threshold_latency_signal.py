from __future__ import annotations

from src.trading.weather_threshold_latency_signal import build_threshold_latency_signal_report


def _row(
    *,
    bucket_type: str = "ge",
    threshold: float = 23.0,
    side: str = "yes",
    token_id: str = "yes-token",
    market_slug: str = "market-1",
    best_ask: float | None = 0.2,
    best_bid: float | None = 0.1,
    ask_depth: float = 10.0,
    spread: float = 0.01,
):
    return {
        "market_slug": market_slug,
        "token_id": token_id,
        "side": side,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "target_date": "2026-06-29",
        "settlement_station_code": "UUWW",
        "settlement_source": "metar",
        "order_book": {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "ask_depth_usdc_3c": ask_depth,
            "bid_depth_usdc_3c": 10.0,
            "asks": [] if best_ask is None else [{"price": best_ask, "size": ask_depth}],
            "bids": [] if best_bid is None else [{"price": best_bid, "size": 10.0}],
        },
    }


def _obs(temp: float, *, available_at: str):
    return {
        "source": "aviationweather_metar_recent_72h",
        "settlement_source": "metar",
        "snapshot_type": "observation",
        "station_code": "UUWW",
        "target_date": "2026-06-29",
        "target_date_local": "2026-06-29",
        "available_at": available_at,
        "observed_at": available_at,
        "temperature_c": temp,
        "official_source_flag": True,
    }


def _report(rows, observations, *, generated_at="2026-06-29T10:06:00Z"):
    return build_threshold_latency_signal_report(rows, observations=observations, generated_at=generated_at)


def test_ge_just_crossed_generates_candidate_when_ask_available():
    report = _report([_row(bucket_type="ge", threshold=23, side="yes")], [_obs(22, available_at="2026-06-29T10:00:00Z"), _obs(23, available_at="2026-06-29T10:05:00Z")])
    row = report["rows"][0]

    assert row["signal_type"] == "ge_just_crossed"
    assert row["side_to_buy"] == "YES"
    assert row["decision"] == "candidate"
    assert report["summary"]["candidate_count"] == 1


def test_le_just_broken_generates_candidate_when_no_side_ask_available():
    rows = [
        _row(bucket_type="le", threshold=23, side="yes", token_id="yes-token", best_ask=0.8),
        _row(bucket_type="le", threshold=23, side="no", token_id="no-token", best_ask=0.2),
    ]
    report = _report(rows, [_obs(23, available_at="2026-06-29T10:00:00Z"), _obs(24, available_at="2026-06-29T10:05:00Z")])
    no_row = [row for row in report["rows"] if row["token_id"] == "no-token"][0]

    assert no_row["signal_type"] == "le_just_broken"
    assert no_row["side_to_buy"] == "NO"
    assert no_row["decision"] == "candidate"


def test_near_cross_not_locked_uses_conservative_probability():
    report = _report(
        [_row(bucket_type="ge", threshold=23, best_ask=0.6)],
        [_obs(22.0, available_at="2026-06-29T10:00:00Z"), _obs(22.4, available_at="2026-06-29T10:05:00Z")],
    )
    row = report["rows"][0]

    assert row["signal_type"] == "ge_near_cross"
    assert row["fair_probability"] == 0.65
    assert row["latency_edge_estimate"] == 0.045
    assert row["decision"] == "candidate"


def test_excludes_eq_and_dust():
    report = _report(
        [
            _row(bucket_type="eq", threshold=23, best_ask=0.2, token_id="eq-token"),
            _row(bucket_type="ge", threshold=23, best_ask=0.001, token_id="dust-token"),
        ],
        [_obs(22.0, available_at="2026-06-29T10:00:00Z"), _obs(22.9, available_at="2026-06-29T10:05:00Z")],
    )
    eq_row = [row for row in report["rows"] if row["bucket_type"] == "eq"][0]
    dust_row = [row for row in report["rows"] if row["token_id"] == "dust-token"][0]

    assert eq_row["decision"] != "candidate"
    assert "bucket_type_not_alpha" in eq_row["blockers"]
    assert dust_row["decision"] != "candidate"
    assert "dust_price" in dust_row["blockers"]


def test_stale_metar_update_blocks_latency_signal():
    report = _report(
        [_row(bucket_type="ge", threshold=23)],
        [_obs(22, available_at="2026-06-29T09:00:00Z"), _obs(23, available_at="2026-06-29T09:01:00Z")],
        generated_at="2026-06-29T10:06:00Z",
    )
    row = report["rows"][0]

    assert row["decision"] != "candidate"
    assert "stale_metar_update" in row["blockers"]


def test_anomaly_blocks_latency_signal():
    report = _report(
        [_row(bucket_type="ge", threshold=23)],
        [_obs(20, available_at="2026-06-29T10:00:00Z"), _obs(35, available_at="2026-06-29T10:05:00Z")],
    )
    row = report["rows"][0]

    assert row["signal_type"] == "ge_just_crossed"
    assert row["decision"] != "candidate"
    assert "blocked_by_observation_anomaly" in row["blockers"]
