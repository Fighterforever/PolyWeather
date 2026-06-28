from __future__ import annotations

from src.weather.historical_forecast_backfill import forecast_row_from_open_meteo_payload


def test_open_meteo_forecast_row_is_no_lookahead_paper_only():
    row = forecast_row_from_open_meteo_payload(
        {
            "latitude": 55.5,
            "longitude": 37.2,
            "generationtime_ms": 1,
            "hourly": {
                "time": ["2026-06-20T00:00", "2026-06-20T01:00"],
                "temperature_2m": [18.0, 21.0],
            },
        },
        station_code="UUWW",
        latitude=55.5,
        longitude=37.2,
        target_date="2026-06-20",
        model="gfs_seamless",
    )

    assert row["source"] == "open_meteo_historical_forecast"
    assert row["predicted_daily_high"] == 21.0
    assert row["available_at"] == "2026-06-20T00:00:00Z"
    assert row["no_lookahead"] is True
    assert row["paper_only"] is True
