from __future__ import annotations

from src.trading.polymarket_alpha.weather_city_regime import build_weather_city_regime


def test_city_regime_outputs_insufficient_gap_for_required_cities():
    report = build_weather_city_regime(family_catalog={"families": [{"city": "ankara"}]}, observations=[])
    cities = {row["city"]: row for row in report["city_regimes"]}

    for city in ["beijing", "hong kong", "london", "moscow", "ankara", "istanbul"]:
        assert city in cities
        assert cities[city]["volatility_bucket"] == "insufficient_data"
    assert report["live_order_path"] is False


def test_city_regime_profiles_with_enough_station_samples():
    obs = [{"station_code": "LTAC", "daily_high": 30 + (i % 2)} for i in range(10)]
    report = build_weather_city_regime(family_catalog={"families": [{"city": "ankara"}]}, observations=obs)
    ankara = [row for row in report["city_regimes"] if row["city"] == "ankara"][0]

    assert ankara["sample_count"] == 10
    assert ankara["volatility_bucket"] in {"stable", "normal", "volatile"}
