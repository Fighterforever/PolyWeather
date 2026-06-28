from __future__ import annotations

from src.trading.weather_station_confusion_edge import (
    build_station_bias_table,
    build_station_confusion_edge_report,
)
from scripts.weather_station_confusion_bias_backfill import build_station_confusion_bias_backfill_report


def _station_rows(count: int = 3):
    return [
        {
            "city": "moscow",
            "station_code": "UUWW",
            "settlement_source": "metar",
            "target_date": f"2026-06-{20 + index:02d}",
            "station_final_high": 26.0 + index,
        }
        for index in range(count)
    ]


def _city_rows(count: int = 3):
    return [
        {
            "city": "moscow",
            "station_code": "UUWW",
            "settlement_source": "metar",
            "target_date": f"2026-06-{20 + index:02d}",
            "city_grid_final_high": 24.0 + index,
        }
        for index in range(count)
    ]


def _active_row(**overrides):
    row = {
        "market_family": "temperature",
        "market_slug": "highest-temperature-in-moscow-on-june-30-2026-25corabove",
        "event_slug": "highest-temperature-in-moscow-on-june-30-2026",
        "token_id": "yes-token",
        "side": "yes",
        "best_ask": 0.10,
        "ask_depth": 10.0,
        "market_implied_de_vig_yes_probability": 0.12,
        "station_forecast_high_c": 26.0,
        "city_grid_forecast_high_c": 24.0,
        "market_bucket": {"bucket_type": "ge", "threshold": 25.0},
        "settlement_spec": {
            "city": "moscow",
            "station_code": "UUWW",
            "settlement_source": "metar",
            "target_date": "2026-06-30",
            "bucket_type": "ge",
            "threshold": 25.0,
        },
    }
    row.update(overrides)
    return row


def test_station_bias_computed_from_station_and_city_high():
    table = build_station_bias_table(station_rows=_station_rows(), city_grid_rows=_city_rows(), generated_at="2026-06-28T00:00:00Z")

    assert table["station_bias_sample_count"] == 3
    assert table["station_biases"][0]["station_code"] == "UUWW"
    assert table["station_biases"][0]["historical_bias_mean"] == 2.0
    assert table["station_biases"][0]["bias_direction"] == "station_hotter"


def test_station_confusion_candidate_when_market_matches_city_not_station():
    report = build_station_confusion_edge_report(
        [_active_row()],
        station_rows=_station_rows(),
        city_grid_rows=_city_rows(),
        generated_at="2026-06-28T00:00:00Z",
        min_bias_sample_count=3,
    )

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["station_probability"] == 1.0
    assert candidate["city_probability"] == 0.0
    assert candidate["station_edge"] > 0
    assert candidate["live_order_path"] is False


def test_no_candidate_when_bias_sample_too_small():
    report = build_station_confusion_edge_report(
        [_active_row()],
        station_rows=_station_rows(count=1),
        city_grid_rows=_city_rows(count=1),
        generated_at="2026-06-28T00:00:00Z",
        min_bias_sample_count=3,
    )

    assert report["candidate_count"] == 0
    assert report["no_candidate_reason_counts"]["bias_sample_too_small"] == 1


def test_excludes_eq_and_dust():
    eq = _active_row(market_bucket={"bucket_type": "eq", "threshold": 25.0}, settlement_spec={"city": "moscow", "station_code": "UUWW", "settlement_source": "metar", "bucket_type": "eq", "threshold": 25.0})
    dust = _active_row(best_ask=0.004)

    report = build_station_confusion_edge_report(
        [eq, dust],
        station_rows=_station_rows(),
        city_grid_rows=_city_rows(),
        generated_at="2026-06-28T00:00:00Z",
        min_bias_sample_count=3,
    )

    assert report["candidate_count"] == 0
    assert report["no_candidate_reason_counts"]["bucket_not_ge_le"] == 1
    assert report["no_candidate_reason_counts"]["dust_price"] == 1


def test_station_confusion_bias_backfill_builds_minimal_active_station_table():
    active = [
        {
            "market_family": "temperature",
            "side": "yes",
            "market_bucket": {"bucket_type": "ge", "threshold": 25.0},
            "settlement_spec": {
                "station_code": "UUWW",
                "settlement_source": "metar",
                "target_date": "2026-06-30",
                "bucket_type": "ge",
                "threshold": 25.0,
            },
        }
    ]
    station_rows = [
        {"station_code": "UUWW", "settlement_source": "metar", "target_date": f"2026-06-{20 + index:02d}", "temperature_c": 26.0 + index}
        for index in range(3)
    ]
    city_rows = [
        {"station_code": "UUWW", "source": "open_meteo_historical_forecast", "target_date": f"2026-06-{20 + index:02d}", "predicted_daily_high": 24.0 + index}
        for index in range(3)
    ]

    report = build_station_confusion_bias_backfill_report(
        active_rows=active,
        station_observation_rows=station_rows,
        city_grid_rows=city_rows,
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["active_station_count"] == 1
    assert report["station_bias_sample_count"] == 3
    pair = report["station_pairs"][0]
    assert pair["city"] == "moscow"
    assert pair["station_code"] == "UUWW"
    assert pair["sample_count"] == 3
    assert pair["station_minus_city_bias_mean"] == 2.0
    assert pair["gap_reason"] == "bias_sample_too_small"


def test_station_confusion_bias_backfill_reports_station_level_gap_reason():
    active = [
        {
            "market_family": "temperature",
            "side": "yes",
            "market_bucket": {"bucket_type": "ge", "threshold": 25.0},
            "settlement_spec": {
                "station_code": "UUWW",
                "settlement_source": "metar",
                "target_date": "2026-06-30",
                "bucket_type": "ge",
                "threshold": 25.0,
            },
        }
    ]

    report = build_station_confusion_bias_backfill_report(
        active_rows=active,
        station_observation_rows=[],
        city_grid_rows=[],
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["station_bias_sample_count"] == 0
    assert report["station_pairs"][0]["gap_reason"] == "missing_station_and_city_grid_history"
    assert report["status"] == "station_confusion_data_unavailable_reduce_priority"
