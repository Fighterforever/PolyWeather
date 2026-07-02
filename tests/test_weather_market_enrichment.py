from __future__ import annotations

from src.trading.weather_market_enrichment import (
    enrich_polymarket_payload_with_scan_models,
    parse_temperature_outcome_spec,
    probability_for_temperature_spec,
)


def test_parse_temperature_outcome_spec_from_polymarket_question():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in Seoul on June 27?",
            "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
            "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28corabove",
            "end_date": "2026-06-27T12:00:00Z",
        }
    )

    assert spec is not None
    assert spec.city == "seoul"
    assert spec.target_date == "2026-06-27"
    assert spec.threshold == 28.0
    assert spec.comparator == "ge"
    assert spec.unit == "C"


def test_parse_temperature_outcome_spec_from_slug_only_date_and_condition():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in Seoul",
            "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
            "end_date": "2026-06-27T12:00:00Z",
        }
    )

    assert spec is not None
    assert spec.city == "seoul"
    assert spec.target_date == "2026-06-27"
    assert spec.threshold == 28.0
    assert spec.comparator == "ge"
    assert spec.unit == "C"


def test_probability_for_temperature_spec_inverts_no_side():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in Seoul on June 27?",
            "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
            "end_date": "2026-06-27T12:00:00Z",
        }
    )
    distribution = [
        {"value": 27, "probability": 0.30},
        {"value": 28, "probability": 0.45},
        {"value": 29, "probability": 0.25},
    ]

    assert probability_for_temperature_spec(distribution, spec, outcome="Yes") == 0.70
    assert probability_for_temperature_spec(distribution, spec, outcome="No") == 0.30


def test_parse_exact_temperature_bucket_from_question_and_slug():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in Ankara on June 26?",
            "question": "Will the highest temperature in Ankara be 26°C on June 26?",
            "market_slug": "highest-temperature-in-ankara-on-june-26-2026-26c",
            "end_date": "2026-06-26T12:00:00Z",
        }
    )

    assert spec is not None
    assert spec.city == "ankara"
    assert spec.target_date == "2026-06-26"
    assert spec.threshold == 26.0
    assert spec.comparator == "eq"
    assert spec.unit == "C"


def test_probability_for_exact_temperature_bucket():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in Ankara on June 26?",
            "question": "Will the highest temperature in Ankara be 26°C on June 26?",
            "market_slug": "highest-temperature-in-ankara-on-june-26-2026-26c",
            "end_date": "2026-06-26T12:00:00Z",
        }
    )
    distribution = [
        {"value": 25, "probability": 0.20},
        {"value": 26, "probability": 0.35},
        {"value": 27, "probability": 0.45},
    ]

    assert probability_for_temperature_spec(distribution, spec, outcome="Yes") == 0.35
    assert probability_for_temperature_spec(distribution, spec, outcome="No") == 0.65


def test_parse_temperature_range_bucket_with_city_alias_and_fahrenheit():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in NYC on June 25?",
            "question": "Will the highest temperature in New York City be between 86-87°F on June 25?",
            "market_slug": "highest-temperature-in-nyc-on-june-25-2026-between-86-87f",
            "end_date": "2026-06-25T12:00:00Z",
        }
    )

    assert spec is not None
    assert spec.city == "new york"
    assert spec.target_date == "2026-06-25"
    assert spec.threshold == 86.0
    assert spec.upper_threshold == 87.0
    assert spec.comparator == "range"
    assert spec.unit == "F"


def test_probability_for_temperature_range_bucket():
    spec = parse_temperature_outcome_spec(
        {
            "event_title": "Highest temperature in NYC on June 25?",
            "question": "Will the highest temperature in New York City be between 86-87°F on June 25?",
            "market_slug": "highest-temperature-in-nyc-on-june-25-2026-between-86-87f",
            "end_date": "2026-06-25T12:00:00Z",
        }
    )
    distribution = [
        {"value": 85, "probability": 0.20},
        {"value": 86, "probability": 0.25},
        {"value": 87, "probability": 0.35},
        {"value": 88, "probability": 0.20},
    ]

    assert probability_for_temperature_spec(distribution, spec, outcome="Yes") == 0.60
    assert probability_for_temperature_spec(distribution, spec, outcome="No") == 0.40


def test_enrich_polymarket_payload_with_scan_models_computes_edge():
    polymarket_payload = {
        "snapshot_id": "poly",
        "status": "ready",
        "diagnostics": {},
        "rows": [
            {
                "id": "yes-row",
                "event_title": "Highest temperature in Seoul on June 27?",
                "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28corabove",
                "end_date": "2026-06-27T12:00:00Z",
                "outcome": "Yes",
                "side": "yes",
                "price": 0.52,
            },
            {
                "id": "no-row",
                "event_title": "Highest temperature in Seoul on June 27?",
                "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28corabove",
                "end_date": "2026-06-27T12:00:00Z",
                "outcome": "No",
                "side": "no",
                "price": 0.48,
            },
        ],
    }
    scan_payload = {
        "snapshot_id": "scan",
        "status": "ready",
        "rows": [
            {
                "id": "seoul:2026-06-27",
                "city": "seoul",
                "local_date": "2026-06-27",
                "deb_prediction": 28.2,
                "distribution_full": [
                    {"value": 27, "probability": 0.30},
                    {"value": 28, "probability": 0.45},
                    {"value": 29, "probability": 0.25},
                ],
            }
        ],
    }

    enriched = enrich_polymarket_payload_with_scan_models(polymarket_payload, scan_payload)

    assert enriched["diagnostics"]["model_join"]["joined"] == 2
    assert enriched["diagnostics"]["market_implied"]["threshold_cdf_group_count"] == 1
    assert enriched["rows"][0]["model_join_status"] == "joined"
    assert enriched["rows"][0]["city"] == "seoul"
    assert enriched["rows"][0]["bucket_label"] == ">= 28°C"
    assert enriched["rows"][0]["settlement_spec_status"] == "supported"
    assert enriched["rows"][0]["settlement_spec"]["station_code"] == "RKSI"
    assert enriched["rows"][0]["market_bucket"]["bucket_type"] == "ge"
    assert enriched["rows"][0]["model_probability"] == 0.70
    assert enriched["rows"][0]["edge_percent"] == 18.0
    assert enriched["rows"][0]["p_lcb"] > 0
    assert enriched["rows"][0]["q_effective"] == 0.52
    assert enriched["rows"][0]["ev_safe"] > 0
    assert enriched["rows"][0]["market_implied_yes_price"] == 0.52
    assert enriched["rows"][0]["market_implied_cdf_raw"] == 0.48
    assert enriched["rows"][0]["market_implied_bucket_family"] == "threshold_cdf"
    assert enriched["rows"][1]["model_probability"] == 0.30
    assert enriched["rows"][1]["edge_percent"] == -18.0


def test_enrich_temperature_without_complete_settlement_spec_is_not_joined():
    enriched = enrich_polymarket_payload_with_scan_models(
        {
            "snapshot_id": "poly",
            "status": "ready",
            "diagnostics": {},
            "rows": [
                {
                    "id": "yes-row",
                    "event_title": "Highest temperature in Seoul on June 27?",
                    "question": "Will the highest temperature in Seoul be 28C or above on June 27?",
                    "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                    "outcome": "Yes",
                    "side": "yes",
                    "price": 0.52,
                }
            ],
        },
        {
            "snapshot_id": "scan",
            "status": "ready",
            "rows": [
                {
                    "id": "seoul:2026-06-27",
                    "city": "seoul",
                    "local_date": "2026-06-27",
                    "distribution_full": [{"value": 28, "probability": 1.0}],
                }
            ],
        },
    )

    assert enriched["diagnostics"]["model_join"]["joined"] == 0
    assert enriched["diagnostics"]["model_join"]["unsupported_settlement_spec"] == 1
    assert enriched["rows"][0]["model_join_status"] == "unsupported_settlement_spec"
    assert "missing_end_time" in enriched["rows"][0]["settlement_spec_unsupported_reasons"]
    assert "edge_percent" not in enriched["rows"][0]


def test_enrich_exact_temperature_bucket_with_scan_models():
    enriched = enrich_polymarket_payload_with_scan_models(
        {
            "snapshot_id": "poly",
            "status": "ready",
            "diagnostics": {},
            "rows": [
                {
                    "id": "yes-row",
                    "event_title": "Highest temperature in Ankara on June 26?",
                    "question": "Will the highest temperature in Ankara be 26°C on June 26?",
                    "market_slug": "highest-temperature-in-ankara-on-june-26-2026-26c",
                    "end_date": "2026-06-26T12:00:00Z",
                    "outcome": "Yes",
                    "side": "yes",
                    "price": 0.12,
                }
            ],
        },
        {
            "snapshot_id": "scan",
            "status": "ready",
            "rows": [
                {
                    "id": "ankara:2026-06-26",
                    "city": "ankara",
                    "local_date": "2026-06-26",
                    "distribution_full": [
                        {"value": 25, "probability": 0.20},
                        {"value": 26, "probability": 0.35},
                        {"value": 27, "probability": 0.45},
                    ],
                }
            ],
        },
    )

    assert enriched["diagnostics"]["model_join"]["joined"] == 1
    assert enriched["rows"][0]["model_join_status"] == "joined"
    assert enriched["rows"][0]["bucket_label"] == "= 26°C"
    assert enriched["rows"][0]["model_probability"] == 0.35
    assert enriched["rows"][0]["edge_percent"] == 23.0


def test_enrich_temperature_range_bucket_with_scan_models():
    enriched = enrich_polymarket_payload_with_scan_models(
        {
            "snapshot_id": "poly",
            "status": "ready",
            "diagnostics": {},
            "rows": [
                {
                    "id": "yes-row",
                    "event_title": "Highest temperature in NYC on June 25?",
                    "question": "Will the highest temperature in New York City be between 86-87°F on June 25?",
                    "market_slug": "highest-temperature-in-nyc-on-june-25-2026-between-86-87f",
                    "end_date": "2026-06-25T12:00:00Z",
                    "outcome": "Yes",
                    "side": "yes",
                    "price": 0.40,
                }
            ],
        },
        {
            "snapshot_id": "scan",
            "status": "ready",
            "rows": [
                {
                    "id": "new-york:2026-06-25",
                    "city": "new york",
                    "local_date": "2026-06-25",
                    "distribution_full": [
                        {"value": 85, "probability": 0.20},
                        {"value": 86, "probability": 0.25},
                        {"value": 87, "probability": 0.35},
                        {"value": 88, "probability": 0.20},
                    ],
                }
            ],
        },
    )

    assert enriched["diagnostics"]["model_join"]["joined"] == 1
    assert enriched["rows"][0]["model_join_status"] == "joined"
    assert enriched["rows"][0]["bucket_label"] == "86-87°F"
    assert enriched["rows"][0]["model_probability"] == 0.60
    assert enriched["rows"][0]["edge_percent"] == 20.0


def test_enrich_marks_unsupported_non_city_weather_markets():
    enriched = enrich_polymarket_payload_with_scan_models(
        {
            "rows": [
                {
                    "event_title": "Will any Category 5 hurricane make landfall in the US in before 2027?",
                    "question": "Will any Category 5 hurricane make landfall in the US in before 2027?",
                    "outcome": "Yes",
                    "price": 0.15,
                }
            ]
        },
        {"rows": []},
    )

    assert enriched["diagnostics"]["model_join"]["joined"] == 0
    assert enriched["diagnostics"]["model_join"]["unsupported_market_type"] == 1
    assert enriched["rows"][0]["model_join_status"] == "unsupported_market_type"
