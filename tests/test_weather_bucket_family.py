from __future__ import annotations

from src.trading.weather_bucket_family import build_weather_bucket_family_catalog


def _row(
    *,
    event_slug="highest-temperature-in-test-city-on-june-28",
    market_slug="m",
    station_code="TEST",
    target_date="2026-06-28",
    bucket_type="eq",
    threshold=25,
    side="yes",
    token_id=None,
):
    return {
        "market_family": "temperature",
        "event_slug": event_slug,
        "market_slug": market_slug,
        "city": "Test City",
        "station_code": station_code,
        "settlement_source": "metar",
        "timezone": "UTC",
        "target_date": target_date,
        "bucket_type": bucket_type,
        "threshold": threshold,
        "side": side,
        "token_id": token_id or f"{market_slug}-{side}",
        "best_ask": 0.25,
        "ask_depth_usdc_3c": 3.0,
        "spread": 0.01,
        "settlement_spec_status": "supported",
        "settlement_spec": {
            "station_code": station_code,
            "settlement_source": "metar",
            "target_date": target_date,
            "timezone": "UTC",
        },
    }


def _complete_rows():
    buckets = [
        ("m-le24", "le", 24),
        ("m-eq25", "eq", 25),
        ("m-eq26", "eq", 26),
        ("m-ge27", "ge", 27),
    ]
    rows = []
    for market_slug, bucket_type, threshold in buckets:
        rows.append(_row(market_slug=market_slug, bucket_type=bucket_type, threshold=threshold, side="yes"))
        rows.append(_row(market_slug=market_slug, bucket_type=bucket_type, threshold=threshold, side="no"))
    return rows


def test_bucket_family_catalog_builds_complete_partition():
    report = build_weather_bucket_family_catalog(_complete_rows())

    assert report["family_count"] == 1
    assert report["partition_candidate_count"] == 1
    family = report["families"][0]
    assert family["event_slug"] == "highest-temperature-in-test-city-on-june-28"
    assert family["station_code"] == "TEST"
    assert family["bucket_count"] == 4
    assert family["has_lower_tail"] is True
    assert family["has_upper_tail"] is True
    assert family["exact_bucket_count"] == 2
    assert family["market_slugs"] == ["m-le24", "m-eq25", "m-eq26", "m-ge27"]
    assert len(family["yes_token_ids"]) == 4
    assert len(family["no_token_ids"]) == 4
    assert family["live_order_path"] is False


def test_bucket_family_catalog_does_not_mix_station_or_date():
    rows = _complete_rows()
    rows.extend(
        [
            _row(event_slug="highest-temperature-in-test-city-on-june-29", market_slug="m2-le24", target_date="2026-06-29", bucket_type="le", threshold=24),
            _row(event_slug="highest-temperature-in-test-city-on-june-29", market_slug="m2-ge25", target_date="2026-06-29", bucket_type="ge", threshold=25),
        ]
    )

    report = build_weather_bucket_family_catalog(rows)

    assert report["family_count"] == 2
    assert sorted(family["target_date"] for family in report["families"]) == ["2026-06-28", "2026-06-29"]


def test_incomplete_bucket_family_is_diagnostic_only():
    rows = [
        _row(market_slug="m-le24", bucket_type="le", threshold=24),
        _row(market_slug="m-eq25", bucket_type="eq", threshold=25),
    ]

    report = build_weather_bucket_family_catalog(rows)

    assert report["partition_candidate_count"] == 0
    family = report["families"][0]
    assert family["is_partition_candidate"] is False
    assert "missing_or_multiple_upper_tail" in family["partition_gap_reasons"]
    assert report["gaps"]
