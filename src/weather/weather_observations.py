from __future__ import annotations

from typing import Any, Dict, Optional

from src.weather.station_registry import StationSpec, station_for_city
from src.weather.weather_sources import WeatherSnapshot, build_weather_snapshot


def _station_code(station: StationSpec | Dict[str, Any] | str) -> str:
    if isinstance(station, StationSpec):
        return station.station_code
    if isinstance(station, dict):
        return str(station.get("station_code") or "")
    return str(station or "")


def build_station_forecast_snapshot(
    *,
    station: StationSpec | Dict[str, Any] | str,
    source: str,
    target_date: str,
    available_at: str,
    forecast_payload: Dict[str, Any],
    issued_at: Optional[str] = None,
    model: Optional[str] = None,
) -> WeatherSnapshot:
    return build_weather_snapshot(
        source=source,
        snapshot_type="forecast",
        station_code=_station_code(station),
        target_date=target_date,
        available_at=available_at,
        issued_at=issued_at,
        model=model,
        payload=forecast_payload,
    )


def build_station_observation_snapshot(
    *,
    station: StationSpec | Dict[str, Any] | str,
    source: str,
    target_date: str,
    available_at: str,
    observed_at: str,
    observation_payload: Dict[str, Any],
) -> WeatherSnapshot:
    return build_weather_snapshot(
        source=source,
        snapshot_type="observation",
        station_code=_station_code(station),
        target_date=target_date,
        available_at=available_at,
        observed_at=observed_at,
        payload=observation_payload,
    )


def build_station_settlement_snapshot(
    *,
    city: str,
    target_date: str,
    available_at: str,
    official_final_value: float,
    unit: str,
    rule_hash: Optional[str] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> WeatherSnapshot:
    station = station_for_city(city)
    if station is None:
        raise ValueError(f"unsupported station city: {city}")
    return build_weather_snapshot(
        source=station.settlement_source,
        snapshot_type="settlement",
        station_code=station.station_code,
        target_date=target_date,
        available_at=available_at,
        payload={
            "city": station.city,
            "official_final_value": float(official_final_value),
            "unit": str(unit),
            "rule_hash": rule_hash,
            "raw": dict(raw_payload or {}),
        },
    )
