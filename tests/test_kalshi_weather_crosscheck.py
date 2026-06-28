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


def test_kalshi_crosscheck_rejects_sports_tickers(monkeypatch):
    monkeypatch.setattr(
        kalshi_weather_crosscheck,
        "fetch_kalshi_markets",
        lambda *args, **kwargs: {
            "markets": [
                {
                    "ticker": "KXWNBAREB",
                    "title": "Will a WNBA player record 10 rebounds?",
                    "yes_bid": 40,
                    "yes_ask": 60,
                }
            ]
        },
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

    assert report["weather_reference_count"] == 0
    assert report["non_weather_kalshi_excluded_count"] == 1
    assert report["gaps"][0]["weather_filter_passed"] is False
    assert report["gaps"][0]["exclusion_reason"] == "excluded_sports_kalshi_market"


def test_kalshi_crosscheck_accepts_weather_temperature_titles(monkeypatch):
    monkeypatch.setattr(
        kalshi_weather_crosscheck,
        "fetch_kalshi_markets",
        lambda *args, **kwargs: {
            "markets": [
                {
                    "ticker": "KXHIGHNYC",
                    "title": "Will the high temperature in New York be above 80 degrees?",
                    "yes_bid": 40,
                    "yes_ask": 60,
                }
            ]
        },
    )

    report = kalshi_weather_crosscheck.build_kalshi_weather_crosscheck(
        [
            {
                "market_slug": "m",
                "station_code": "KLGA",
                "target_date": "2026-06-20",
                "threshold": 80,
                "settled_probability_by_outcome": {"Yes": 1.0},
            }
        ]
    )

    assert report["weather_reference_count"] == 1
    assert report["rows"][0]["weather_filter_passed"] is True
    assert report["rows"][0]["kalshi_market_family"] == "weather"


def test_kalshi_crosscheck_no_probability_diff_for_non_weather(monkeypatch):
    monkeypatch.setattr(
        kalshi_weather_crosscheck,
        "fetch_kalshi_markets",
        lambda *args, **kwargs: {
            "markets": [{"ticker": "KXOTHER", "title": "Will a movie win an award?", "yes_bid": 40, "yes_ask": 60}]
        },
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

    assert report["rows"] == []
    assert all("cross_market_probability_diff" not in gap for gap in report["gaps"])
