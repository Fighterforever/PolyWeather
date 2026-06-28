from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional

from src.weather.station_registry import StationSpec, station_for_city
from src.weather.weather_sources import WeatherSnapshot, build_weather_snapshot, parse_utc


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


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def observation_temperature_value(snapshot: Dict[str, Any]) -> Optional[float]:
    payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
    for field in (
        "temperature_c",
        "temp_c",
        "current_temperature_c",
        "temperature",
        "temp",
        "official_current_high",
        "current_high",
        "max_temp_c",
    ):
        value = _safe_float(payload.get(field))
        if value is not None:
            return value
    return None


def observation_is_official(snapshot: Dict[str, Any]) -> bool:
    payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
    for field in ("official_source_flag", "official_source", "is_official"):
        if field in snapshot:
            return bool(snapshot.get(field))
        if field in payload:
            return bool(payload.get(field))
    return str(snapshot.get("source") or "").strip().lower() in {"metar", "nws", "noaa", "aeroweb", "wunderground"}


def detect_station_observation_anomalies(
    snapshots: Iterable[WeatherSnapshot | Dict[str, Any]],
    *,
    max_jump_c_10m: float = 8.0,
) -> List[str]:
    """Return conservative station-level flags for lock-style strategies."""

    rows: List[Dict[str, Any]] = [
        snapshot.to_dict() if isinstance(snapshot, WeatherSnapshot) else dict(snapshot)
        for snapshot in snapshots
    ]
    flags: set[str] = set()
    if any(not observation_is_official(row) for row in rows):
        flags.add("official_source_flag_missing")
    sortable = []
    for row in rows:
        observed_at = parse_utc(row.get("observed_at") or row.get("available_at"))
        temperature = observation_temperature_value(row)
        if observed_at is not None and temperature is not None:
            sortable.append((observed_at, temperature))
    sortable.sort(key=lambda pair: pair[0])
    for (prev_time, prev_temp), (next_time, next_temp) in zip(sortable, sortable[1:]):
        if next_time - prev_time <= timedelta(minutes=10) and abs(next_temp - prev_temp) > float(max_jump_c_10m):
            flags.add("sudden_temperature_spike")
            break
    return sorted(flags)
