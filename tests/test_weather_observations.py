from __future__ import annotations

from src.weather.weather_observations import OfficialIntradayObservationRepository


def _row(observed_at: str, available_at: str, temp: float):
    return {
        "schema_version": "polyweather_official_intraday_observation.v1",
        "station_code": "UUWW",
        "settlement_source": "metar",
        "source": "aviationweather_metar_recent_72h",
        "snapshot_type": "observation",
        "target_date_local": "2026-06-28",
        "observed_at": observed_at,
        "available_at": available_at,
        "temperature_c": temp,
        "quality_flags": [],
        "anomaly_flags": [],
        "paper_only": True,
        "counts_for_live_gate": False,
    }


def test_intraday_repository_returns_only_visible_rows(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")
    repo.append(
        [
            _row("2026-06-28T10:00:00Z", "2026-06-28T10:02:00Z", 20),
            _row("2026-06-28T11:00:00Z", "2026-06-28T11:02:00Z", 23),
            _row("2026-06-28T12:00:00Z", "2026-06-28T12:02:00Z", 25),
        ]
    )

    visible = repo.query(
        station_code="UUWW",
        target_date="2026-06-28",
        replay_time="2026-06-28T11:30:00Z",
    )

    assert [row["temperature_c"] for row in visible] == [20, 23]


def test_current_high_as_of_has_no_lookahead(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "intraday.jsonl")
    repo.append(
        [
            _row("2026-06-28T10:00:00Z", "2026-06-28T10:02:00Z", 20),
            _row("2026-06-28T11:00:00Z", "2026-06-28T11:02:00Z", 23),
            _row("2026-06-28T12:00:00Z", "2026-06-28T12:02:00Z", 25),
        ]
    )

    high = repo.current_high_as_of(
        station_code="UUWW",
        target_date="2026-06-28",
        replay_time="2026-06-28T11:30:00Z",
    )

    assert high["current_high_c"] == 23
    assert high["latest_observation_at"] == "2026-06-28T11:00:00Z"
    assert high["latest_available_at"] == "2026-06-28T11:02:00Z"
    assert high["observation_count"] == 2
    assert high["no_lookahead"] is True
