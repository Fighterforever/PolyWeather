from __future__ import annotations

from src.trading.weather_market_implied import enrich_payload_with_market_implied


def test_market_implied_de_vig_normalizes_mutually_exclusive_temperature_buckets():
    payload = {
        "rows": [
            {
                "event_slug": "highest-temperature-in-seoul-on-june-27-2026",
                "city": "seoul",
                "selected_date": "2026-06-27",
                "market_id": "m-27",
                "side": "yes",
                "market_probability": 0.40,
                "market_bucket": {"bucket_type": "eq", "threshold": 27},
            },
            {
                "event_slug": "highest-temperature-in-seoul-on-june-27-2026",
                "city": "seoul",
                "selected_date": "2026-06-27",
                "market_id": "m-28",
                "side": "yes",
                "market_probability": 0.60,
                "market_bucket": {"bucket_type": "eq", "threshold": 28},
            },
            {
                "event_slug": "highest-temperature-in-seoul-on-june-27-2026",
                "city": "seoul",
                "selected_date": "2026-06-27",
                "market_id": "m-29",
                "side": "yes",
                "market_probability": 0.20,
                "market_bucket": {"bucket_type": "eq", "threshold": 29},
            },
        ]
    }

    enriched = enrich_payload_with_market_implied(payload)

    assert enriched["diagnostics"]["market_implied"]["mutually_exclusive_group_count"] == 1
    assert enriched["rows"][0]["market_implied_overround"] == 1.2
    assert enriched["rows"][0]["market_implied_de_vig_status"] == "normalised"
    assert enriched["rows"][0]["market_implied_de_vig_yes_probability"] == 0.333333
    assert enriched["rows"][1]["market_implied_de_vig_yes_probability"] == 0.5


def test_market_implied_threshold_cdf_monotonic_fit_flags_violations():
    payload = {
        "rows": [
            {
                "event_slug": "highest-temperature-in-seoul-on-june-27-2026",
                "city": "seoul",
                "selected_date": "2026-06-27",
                "market_id": "m-le-27",
                "side": "yes",
                "market_probability": 0.70,
                "market_bucket": {"bucket_type": "le", "threshold": 27},
            },
            {
                "event_slug": "highest-temperature-in-seoul-on-june-27-2026",
                "city": "seoul",
                "selected_date": "2026-06-27",
                "market_id": "m-le-28",
                "side": "yes",
                "market_probability": 0.60,
                "market_bucket": {"bucket_type": "le", "threshold": 28},
            },
        ]
    }

    enriched = enrich_payload_with_market_implied(payload)

    assert enriched["diagnostics"]["market_implied"]["threshold_cdf_group_count"] == 1
    assert enriched["diagnostics"]["market_implied"]["monotonic_violation_group_count"] == 1
    assert enriched["rows"][0]["market_implied_cdf_raw"] == 0.70
    assert enriched["rows"][0]["market_implied_cdf"] == 0.65
    assert enriched["rows"][1]["market_implied_cdf"] == 0.65
    assert enriched["rows"][0]["market_implied_monotonic_violation"] is True
