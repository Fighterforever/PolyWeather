from __future__ import annotations

from src.trading.weather_observation_lock_signal import build_observation_lock_signal_report
from src.weather.weather_observations import OfficialIntradayObservationRepository, detect_station_observation_anomalies


def _row(
    *,
    bucket_type: str = "ge",
    threshold: float = 23.0,
    upper_threshold: float | None = None,
    best_bid: float = 0.30,
    best_ask: float = 0.40,
    spread: float = 0.02,
    ask_depth: float = 10.0,
    bid_depth: float = 10.0,
    station: str = "UUWW",
    source: str = "metar",
):
    return {
        "market_slug": f"highest-temperature-test-{bucket_type}",
        "token_id": "yes-token",
        "side": "yes",
        "bucket_type": bucket_type,
        "threshold": threshold,
        "upper_threshold": upper_threshold,
        "target_date": "2026-06-29",
        "settlement_station_code": station,
        "settlement_source": source,
        "market_close_time": "2026-06-29T12:00:00Z",
        "observation_window_end_time": "2026-06-29T20:59:59Z",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "ask_depth_usdc_3c": ask_depth,
        "bid_depth_usdc_3c": bid_depth,
    }


def _obs(value: float, *, observed_at: str = "2026-06-29T10:00:00Z", source: str = "metar"):
    return {
        "source": source,
        "snapshot_type": "observation",
        "station_code": "UUWW",
        "target_date": "2026-06-29",
        "available_at": observed_at,
        "observed_at": observed_at,
        "payload": {
            "temperature_c": value,
            "official_source_flag": True,
        },
    }


def _first(report):
    return report["rows"][0]


def _report(rows, observations):
    return build_observation_lock_signal_report(
        rows,
        observations=observations,
        generated_at="2026-06-29T10:06:00Z",
    )


def test_ge_bucket_locks_yes_when_current_high_crosses_threshold():
    report = _report([_row(threshold=23, best_ask=0.40)], [_obs(24)])

    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["locked_side"] == "YES"
    assert row["decision"] == "candidate"
    assert row["executable_edge"] == 0.595


def test_le_bucket_current_high_above_threshold_locks_no_side():
    report = _report([_row(bucket_type="le", threshold=24, best_bid=0.20)], [_obs(28)])

    row = _first(report)
    assert row["lock_state"] == "le_yes_dead_no_locked"
    assert row["locked_side"] == "NO"
    assert row["price_semantics"] == "synthetic_no_from_yes_bid"
    assert row["q_effective"] == 0.8


def test_ge_bucket_not_locked_below_threshold():
    report = _report([_row(bucket_type="ge", threshold=23)], [_obs(22)])

    row = _first(report)
    assert row["lock_state"] == "ge_not_locked"
    assert row["decision"] == "reject"
    assert "observation_not_locked" in row["blockers"]


def test_eq_dead_yes_is_shadow_only_not_candidate():
    report = _report([_row(bucket_type="eq", threshold=27, best_bid=0.20)], [_obs(28)])

    row = _first(report)
    assert row["lock_state"] == "eq_yes_dead_no_locked"
    assert row["decision"] == "shadow"
    assert "eq_exact_not_alpha" in row["blockers"]


def test_dust_price_locked_signal_is_shadow_only():
    report = _report([_row(bucket_type="ge", threshold=23, best_ask=0.001)], [_obs(24)])

    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["price_bucket"] == "price_lt_0_005"
    assert row["decision"] == "shadow"
    assert "dust_price_bucket_not_alpha" in row["blockers"]


def test_observation_anomaly_blocks_candidate():
    observations = [
        _obs(20, observed_at="2026-06-29T10:00:00Z"),
        _obs(33, observed_at="2026-06-29T10:05:00Z"),
    ]
    assert detect_station_observation_anomalies(observations) == ["sudden_temperature_spike"]

    report = _report([_row(bucket_type="ge", threshold=23)], observations)
    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["decision"] == "watch"
    assert "blocked_by_observation_anomaly" in row["blockers"]


def test_non_dust_locked_signal_can_candidate_from_repository(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")
    repo.append(
        [
            {
                **_obs(24, observed_at="2026-06-29T10:00:00Z"),
                "source": "aviationweather_metar_recent_72h",
                "settlement_source": "metar",
                "target_date_local": "2026-06-29",
                "temperature_c": 24,
                "quality_flags": [],
                "anomaly_flags": [],
            }
        ]
    )

    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23, best_ask=0.40)],
        intraday_repository=repo,
        generated_at="2026-06-29T10:06:00Z",
    )

    row = _first(report)
    assert row["lock_state"] == "ge_yes_locked"
    assert row["decision"] == "candidate"
    assert row["latest_available_at"] == "2026-06-29T10:00:00Z"
    assert report["summary"]["candidate_count"] == 1
    assert report["summary"]["intraday_visible_station_count"] == 1


def test_no_intraday_blocks_candidate_from_repository(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")

    report = build_observation_lock_signal_report(
        [_row(bucket_type="ge", threshold=23, best_ask=0.40)],
        intraday_repository=repo,
        generated_at="2026-06-29T10:06:00Z",
    )

    row = _first(report)
    assert row["lock_state"] == "missing_intraday_observation"
    assert row["decision"] == "reject"
    assert report["summary"]["missing_intraday_count"] == 1
