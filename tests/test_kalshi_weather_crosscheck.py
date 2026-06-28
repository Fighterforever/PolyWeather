from __future__ import annotations

from src.trading import kalshi_weather_crosscheck


def test_kalshi_crosscheck_outputs_gap_when_no_match(monkeypatch):
    monkeypatch.setattr(kalshi_weather_crosscheck, "fetch_kalshi_markets", lambda *args, **kwargs: {"markets": []})

    report = kalshi_weather_crosscheck.build_kalshi_weather_crosscheck(
        [
            {
                "market_slug": "m",
                "station_code": "UUWW",
                "target_date": "2026-06-20",
                "threshold": 20,
            }
        ]
    )

    assert report["row_count"] == 0
    assert report["gaps"][0]["gap_reason"] == "no_matching_kalshi_market"


def test_kalshi_crosscheck_outputs_probability_diff(monkeypatch):
    monkeypatch.setattr(
        kalshi_weather_crosscheck,
        "fetch_kalshi_markets",
        lambda *args, **kwargs: {"markets": [{"ticker": "KX", "title": "Weather", "yes_bid": 40, "yes_ask": 60}]},
    )

    report = kalshi_weather_crosscheck.build_kalshi_weather_crosscheck(
        [
            {
                "market_slug": "m",
                "station_code": "UUWW",
                "target_date": "2026-06-20",
                "threshold": 20,
                "settled_probability_by_outcome": {"Yes": 1.0},
            }
        ]
    )

    assert report["row_count"] == 1
    assert report["rows"][0]["cross_market_probability_diff"] == 0.5
    assert report["rows"][0]["live_order_path"] is False
