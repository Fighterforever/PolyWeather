from __future__ import annotations

from scripts.weather_observation_lock_historical_replay import build_observation_lock_historical_replay
from src.weather.weather_observations import OfficialIntradayObservationRepository


def _closed_market():
    return {
        "market_slug": "highest-temperature-in-moscow-on-june-20-2026-20corabove",
        "market_family": "temperature",
        "token_id": "yes-token",
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "settled_yes_payout": 1.0,
        "station_code": "UUWW",
        "target_date": "2026-06-20",
        "bucket_type": "ge",
        "threshold": 20.0,
        "settlement_source": "metar",
        "settlement_spec": {
            "station_code": "UUWW",
            "target_date": "2026-06-20",
            "bucket_type": "ge",
            "threshold": 20.0,
            "settlement_source": "metar",
            "market_close_time": "2026-06-20T12:00:00Z",
        },
    }


def _obs(temp: float, available_at: str):
    return {
        "station_code": "UUWW",
        "target_date_local": "2026-06-20",
        "settlement_source": "metar",
        "source": "aviationweather_metar_history",
        "observed_at": available_at,
        "available_at": available_at,
        "temperature_c": temp,
        "quality_flags": [],
        "anomaly_flags": [],
        "paper_only": True,
        "counts_for_live_gate": False,
    }


def test_historical_replay_does_not_calculate_pnl_without_executable_price(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "obs.jsonl")
    repo.append([_obs(21, "2026-06-20T10:00:00Z")])

    report = build_observation_lock_historical_replay(
        closed_markets=[_closed_market()],
        intraday_repository=repo,
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["summary"]["locked_signal_count"] > 0
    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["resolved_pnl_cents"] is None
    assert report["summary"]["missing_historical_executable_price"] is True


def test_historical_replay_scores_only_executable_depth_price(tmp_path):
    repo = OfficialIntradayObservationRepository(tmp_path / "obs.jsonl")
    repo.append([_obs(21, "2026-06-20T10:00:00Z")])

    report = build_observation_lock_historical_replay(
        closed_markets=[_closed_market()],
        intraday_repository=repo,
        price_history_rows=[
            {
                "token_id": "yes-token",
                "timestamp": "2026-06-20T10:05:00Z",
                "price": 0.4,
                "best_ask": 0.4,
                "best_bid": 0.38,
                "spread": 0.02,
                "ask_depth_usdc_3c": 10,
                "executable_depth_available": True,
            }
        ],
        generated_at="2026-06-28T00:00:00Z",
    )

    assert report["summary"]["candidate_count"] > 0
    assert report["summary"]["resolved_fill_count"] > 0
    assert report["summary"]["resolved_pnl_cents"] is not None
    assert report["summary"]["no_lookahead_violation_count"] == 0
