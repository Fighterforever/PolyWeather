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
    }
