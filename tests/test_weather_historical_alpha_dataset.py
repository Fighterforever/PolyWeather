from __future__ import annotations

from src.trading.weather_historical_alpha_dataset import build_weather_historical_alpha_dataset
from src.weather.weather_observations import OfficialIntradayObservationRepository


def test_historical_alpha_dataset_reports_missing_fields(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "obs.jsonl")
    repo.append(
        [
            {
                "station_code": "UUWW",
                "target_date_local": "2026-06-20",
                "settlement_source": "metar",
                "available_at": "2026-06-20T10:00:00Z",
                "observed_at": "2026-06-20T10:00:00Z",
                "temperature_c": 22,
                "quality_flags": [],
                "anomaly_flags": [],
            }
        ]
    )

    report = build_weather_historical_alpha_dataset(
        closed_markets=[
            {
                "market_slug": "m",
                "token_id": "yes-token",
                "station_code": "UUWW",
                "target_date": "2026-06-20",
                "bucket_type": "ge",
                "threshold": 20,
                "settlement_source": "metar",
                "settled_yes_payout": 1,
                "settlement_spec": {"market_close_time": "2026-06-20T12:00:00Z"},
            }
        ],
        intraday_repository=repo,
    )

    assert report["row_count"] == 1
    assert report["rows"][0]["current_high_as_of_replay"] == 22
    assert report["missing_field_counts"]["forecast"] == 1
    assert report["missing_field_counts"]["price"] == 1
