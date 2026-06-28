from __future__ import annotations

from src.weather.metar_intraday_collector import (
    build_intraday_observations_from_api_rows,
    collect_metar_intraday_observations,
    parse_aviationweather_metar_row,
)


def _api_row(station: str = "UUWW", temp: float = 15.0):
    return {
        "icaoId": station,
        "receiptTime": "2026-06-28T04:06:08.000Z",
        "reportTime": "2026-06-28T04:00:00.000Z",
        "temp": temp,
        "rawOb": f"METAR {station} 280400Z 33004MPS 9999 FEW012 {int(temp)}/12 Q1015",
    }


def test_metar_collector_parses_temperature_c():
    row = parse_aviationweather_metar_row(_api_row(temp=17), fetched_at="2026-06-28T04:10:00Z")

    assert row is not None
    assert row["station_code"] == "UUWW"
    assert row["temperature_c"] == 17.0
    assert row["raw_metar"].startswith("METAR UUWW")
    assert row["settlement_source"] == "metar"
    assert row["source"] == "aviationweather_metar_recent_72h"


def test_intraday_observation_has_available_at():
    row = parse_aviationweather_metar_row(_api_row(), fetched_at="2026-06-28T04:10:00Z")

    assert row is not None
    assert row["available_at"] == "2026-06-28T04:06:08Z"
    assert row["available_at"] <= row["fetched_at"]
    assert row["target_date_local"] == "2026-06-28"
    assert row["paper_only"] is True
    assert row["counts_for_live_gate"] is False


def test_collector_does_not_use_final_value_as_intraday():
    report = build_intraday_observations_from_api_rows(
        [{"icaoId": "UUWW", "official_final_value": 99.0, "receiptTime": "2026-06-28T04:06:08Z"}],
        fetched_at="2026-06-28T04:10:00Z",
        station_codes=["UUWW"],
    )

    assert report["observation_count"] == 1
    assert report["observations"][0]["temperature_c"] is None
    assert "missing_temperature_c" in report["observations"][0]["quality_flags"]


def test_missing_metar_returns_gap_reason():
    report = build_intraday_observations_from_api_rows(
        [_api_row("EGLC", temp=20)],
        fetched_at="2026-06-28T04:10:00Z",
        station_codes=["LTAC"],
    )

    assert report["observation_count"] == 0
    assert report["gaps"] == [
        {
            "station_code": "LTAC",
            "settlement_source": "metar",
            "source": "aviationweather_metar_recent_72h",
            "gap_reason": "missing_metar_rows_from_source",
        }
    ]
