from __future__ import annotations

import pytest

from src.weather.station_registry import active_supported_metar_station_manifest, station_for_city, station_registry_snapshot
from src.weather.weather_observations import (
    build_station_forecast_snapshot,
    build_station_observation_snapshot,
    build_station_settlement_snapshot,
)
from src.weather.settlement_truth import daily_high_from_intraday_points, load_official_temperature_value
from src.weather.weather_sources import build_weather_snapshot, snapshots_available_for_replay


def test_station_registry_maps_city_to_settlement_station():
    station = station_for_city("seoul")

    assert station is not None
    assert station.city == "seoul"
    assert station.station_code == "RKSI"
    assert station.settlement_source == "metar"
    assert station.timezone == "UTC+09:00"
    assert "seoul" in station_registry_snapshot()


def test_active_supported_metar_station_manifest_extracts_exact_supported_rows():
    manifest = active_supported_metar_station_manifest(
        [
            {
                "market_slug": "m1",
                "settlement_spec": {
                    "bucket_type": "eq",
                    "station_code": "UUWW",
                    "settlement_source": "metar",
                },
            },
            {
                "market_slug": "m2",
                "settlement_spec": {
                    "bucket_type": "eq",
                    "station_code": "LTFM",
                    "settlement_source": "noaa",
                },
            },
            {
                "market_slug": "m3",
                "settlement_spec": {
                    "bucket_type": "ge",
                    "station_code": "LTAC",
                    "settlement_source": "metar",
                },
            },
        ]
    )

    assert manifest["active_eq_row_count"] == 2
    assert manifest["active_supported_metar_station_count"] == 1
    assert manifest["station_codes"] == ["UUWW"]
    assert manifest["active_supported_official_station_count"] == 2
    assert manifest["supported_official_station_codes"] == ["LTFM", "UUWW"]
    assert manifest["unsupported_eq_rows_by_source"] == []
    assert manifest["legacy_metar_only_unsupported_eq_rows_by_source"] == [{"settlement_source": "noaa", "row_count": 1}]


def test_weather_snapshot_requires_available_at_for_replay_safety():
    with pytest.raises(ValueError, match="available_at"):
        build_weather_snapshot(
            source="open_meteo",
            snapshot_type="forecast",
            station_code="RKSI",
            target_date="2026-06-27",
            available_at="",
            payload={},
        )


def test_snapshots_available_for_replay_filters_future_data():
    station = station_for_city("seoul")
    early = build_station_forecast_snapshot(
        station=station,
        source="open_meteo",
        model="GFS",
        target_date="2026-06-27",
        issued_at="2026-06-27T00:00:00Z",
        available_at="2026-06-27T00:05:00Z",
        forecast_payload={"temperature_2m_max": 28.0},
    )
    future = build_station_observation_snapshot(
        station=station,
        source="metar",
        target_date="2026-06-27",
        observed_at="2026-06-27T04:00:00Z",
        available_at="2026-06-27T04:02:00Z",
        observation_payload={"temp_c": 29.0},
    )

    visible = snapshots_available_for_replay(
        [future, early],
        replay_time="2026-06-27T01:00:00Z",
    )

    assert [row["snapshot_type"] for row in visible] == ["forecast"]
    assert visible[0]["available_at"] == "2026-06-27T00:05:00Z"


def test_build_station_settlement_snapshot_uses_registry_source():
    snapshot = build_station_settlement_snapshot(
        city="hong kong",
        target_date="2026-06-27",
        available_at="2026-06-27T16:00:00Z",
        official_final_value=31.2,
        unit="C",
        rule_hash="rule-hash",
    )

    payload = snapshot.to_dict()
    assert payload["source"] == "hko"
    assert payload["station_code"] == "HKO"
    assert payload["snapshot_type"] == "settlement"
    assert payload["payload"]["official_final_value"] == 31.2


def test_daily_high_from_intraday_points_uses_observed_temperature_max():
    result = daily_high_from_intraday_points(
        [
            {"time": "09:00", "temp": 26.4},
            {"time": "14:00", "temp": 31.2},
            {"time": "15:00", "temp": 30.9},
        ]
    )

    assert result["status"] == "ready"
    assert result["official_final_value"] == 31.2
    assert result["max_observed_at"] == "14:00"
    assert result["observation_count"] == 3


def test_load_official_temperature_value_reads_matching_station_source_date():
    class FakeRepository:
        def load_points(self, *, source_code, station_code, target_date):
            assert source_code == "metar"
            assert station_code == "RKSI"
            assert target_date == "2026-06-26"
            return [
                {"time": "11:00", "temp": 28.0},
                {"time": "15:00", "temp": 30.4},
            ]

    result = load_official_temperature_value(
        city="seoul",
        target_date="2026-06-26",
        repository=FakeRepository(),
    )

    assert result["status"] == "ready"
    assert result["official_final_value"] == 30.4
    assert result["station_code"] == "RKSI"
    assert result["source_code"] == "metar"
