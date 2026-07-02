from __future__ import annotations

from src.weather.metar_update_events import detect_high_update_events


def _obs(temp: float, *, available_at: str, station: str = "UUWW", target_date: str = "2026-06-29"):
    return {
        "source": "aviationweather_metar_recent_72h",
        "settlement_source": "metar",
        "snapshot_type": "observation",
        "station_code": station,
        "target_date": target_date,
        "target_date_local": target_date,
        "available_at": available_at,
        "observed_at": available_at,
        "temperature_c": temp,
        "official_source_flag": True,
    }


def test_detects_new_daily_high():
    report = detect_high_update_events(
        station_code="UUWW",
        target_date="2026-06-29",
        replay_time="2026-06-29T10:06:00Z",
        observations=[
            _obs(21, available_at="2026-06-29T10:00:00Z"),
            _obs(23, available_at="2026-06-29T10:05:00Z"),
        ],
    )

    assert report["previous_high"] == 21
    assert report["current_high"] == 23
    assert report["high_changed"] is True


def test_detects_threshold_crossing():
    report = detect_high_update_events(
        station_code="UUWW",
        target_date="2026-06-29",
        replay_time="2026-06-29T10:06:00Z",
        observations=[
            _obs(22, available_at="2026-06-29T10:00:00Z"),
            _obs(24, available_at="2026-06-29T10:05:00Z"),
        ],
        thresholds=[23],
    )

    assert {"threshold": 23.0, "direction": "ge_crossed"} in report["crossed_thresholds"]
    assert {"threshold": 23.0, "direction": "le_broken"} in report["crossed_thresholds"]


def test_no_lookahead_update_detection():
    report = detect_high_update_events(
        station_code="UUWW",
        target_date="2026-06-29",
        replay_time="2026-06-29T10:04:00Z",
        observations=[
            _obs(22, available_at="2026-06-29T10:00:00Z"),
            _obs(24, available_at="2026-06-29T10:05:00Z"),
        ],
        thresholds=[23],
    )

    assert report["current_high"] == 22
    assert report["crossed_thresholds"] == []
    assert report["no_lookahead"] is True


def test_no_event_when_high_unchanged():
    report = detect_high_update_events(
        station_code="UUWW",
        target_date="2026-06-29",
        replay_time="2026-06-29T10:06:00Z",
        observations=[
            _obs(23, available_at="2026-06-29T10:00:00Z"),
            _obs(22, available_at="2026-06-29T10:05:00Z"),
        ],
        thresholds=[24],
    )

    assert report["current_high"] == 23
    assert report["high_changed"] is False
    assert report["crossed_thresholds"] == []
