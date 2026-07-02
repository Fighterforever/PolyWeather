from __future__ import annotations

from src.trading.weather_market_catalog import (
    MARKET_BUCKET_SCHEMA_VERSION,
    SETTLEMENT_SPEC_SCHEMA_VERSION,
    build_market_bucket,
    build_temperature_settlement_spec,
)


def test_build_temperature_settlement_spec_for_supported_station_market():
    row = {
        "platform": "polymarket",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "event_slug": "highest-temperature-in-seoul-on-june-27-2026",
        "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
        "end_date": "2026-06-27T12:00:00Z",
    }

    spec, reasons = build_temperature_settlement_spec(
        row,
        city="seoul",
        target_date="2026-06-27",
        bucket_type="ge",
        threshold=28,
        unit="C",
    )
    bucket = build_market_bucket(row, bucket_type="ge", threshold=28, unit="C")

    assert reasons == []
    assert spec is not None
    assert spec.to_dict()["schema_version"] == SETTLEMENT_SPEC_SCHEMA_VERSION
    assert spec.station_code == "RKSI"
    assert spec.settlement_source == "metar"
    assert spec.target_date == "2026-06-27"
    assert spec.end_time == "2026-06-27T12:00:00Z"
    assert spec.market_close_time == "2026-06-27T12:00:00Z"
    assert spec.observation_window_end_time == "2026-06-27T14:59:59Z"
    assert spec.settlement_due_time == "2026-06-27T20:59:59Z"
    assert spec.settlement_grace_hours == 6.0
    assert spec.bucket_type == "ge"
    assert spec.rule_hash
    assert bucket.to_dict()["schema_version"] == MARKET_BUCKET_SCHEMA_VERSION
    assert bucket.label == ">= 28°C"


def test_build_temperature_settlement_spec_returns_diagnostics_for_incomplete_market():
    spec, reasons = build_temperature_settlement_spec(
        {},
        city="unknown-city",
        target_date=None,
        bucket_type="ge",
        threshold=None,
        unit="C",
    )

    assert spec is None
    assert set(reasons) >= {
        "missing_station_code",
        "missing_settlement_source",
        "missing_target_date",
        "missing_threshold",
        "missing_end_time",
        "missing_observation_window_end_time",
        "missing_settlement_due_time",
    }


def test_temperature_settlement_clock_separates_market_close_from_settlement_due():
    row = {
        "platform": "polymarket",
        "market_id": "market-ankara",
        "market_slug": "highest-temperature-in-ankara-on-june-27-2026-24corbelow",
        "event_slug": "highest-temperature-in-ankara-on-june-27-2026",
        "question": "Will the highest temperature in Ankara be 24C or below on June 27?",
        "end_date": "2026-06-27T12:00:00Z",
    }

    spec, reasons = build_temperature_settlement_spec(
        row,
        city="ankara",
        target_date="2026-06-27",
        bucket_type="le",
        threshold=24,
        unit="C",
    )

    assert reasons == []
    assert spec is not None
    assert spec.market_close_time == "2026-06-27T12:00:00Z"
    assert spec.end_time == "2026-06-27T12:00:00Z"
    assert spec.timezone == "UTC+03:00"
    assert spec.observation_window_end_time == "2026-06-27T20:59:59Z"
    assert spec.settlement_due_time == "2026-06-28T02:59:59Z"
    assert spec.settlement_due_time > spec.observation_window_end_time
