from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.weather.station_registry import StationSpec, station_for_city
from src.weather.weather_sources import WeatherSnapshot, build_weather_snapshot, parse_utc, utc_iso


INTRADAY_OBSERVATION_SCHEMA_VERSION = "polyweather_official_intraday_observation.v1"


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
    for field in (
        "temperature_c",
        "temp_c",
        "current_temperature_c",
        "temperature",
        "temp",
        "official_current_high",
        "current_high_c",
        "current_high",
        "max_temp_c",
    ):
        value = _safe_float(snapshot.get(field))
        if value is not None:
            return value
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


def _append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [dict(row) for row in rows if isinstance(row, dict)]
    if not materialized:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(materialized)


def load_intraday_observations(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


class OfficialIntradayObservationRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, observations: Iterable[Dict[str, Any]]) -> int:
        return _append_jsonl(self.path, observations)

    def load(self) -> List[Dict[str, Any]]:
        return load_intraday_observations(self.path)

    def query(
        self,
        *,
        station_code: str,
        target_date: str,
        replay_time: str,
        settlement_source: str = "metar",
    ) -> List[Dict[str, Any]]:
        replay_dt = parse_utc(replay_time)
        if replay_dt is None:
            raise ValueError("replay_time must be an ISO UTC timestamp")
        station = str(station_code or "").strip().upper()
        target = str(target_date or "").strip()
        source = str(settlement_source or "").strip().lower()
        rows: List[Dict[str, Any]] = []
        for row in self.load():
            row_station = str(row.get("station_code") or "").strip().upper()
            row_target = str(row.get("target_date_local") or row.get("target_date") or "").strip()
            row_source = str(row.get("settlement_source") or row.get("source") or "").strip().lower()
            available_at = parse_utc(row.get("available_at"))
            if row_station != station or row_target != target:
                continue
            if source and row_source != source and not (source == "metar" and row_source.startswith("aviationweather_metar")):
                continue
            if available_at is None or available_at > replay_dt:
                continue
            rows.append(row)
        return sorted(rows, key=lambda item: str(item.get("available_at") or ""))

    def current_high_as_of(
        self,
        *,
        station_code: str,
        target_date: str,
        replay_time: str,
        settlement_source: str = "metar",
    ) -> Dict[str, Any]:
        rows = self.query(
            station_code=station_code,
            target_date=target_date,
            replay_time=replay_time,
            settlement_source=settlement_source,
        )
        temperatures = [
            value
            for row in rows
            for value in [observation_temperature_value(row)]
            if value is not None
        ]
        latest_observed_at = None
        latest_available_at = None
        for row in rows:
            observed_at = str(row.get("observed_at") or "").strip()
            available_at = str(row.get("available_at") or "").strip()
            if observed_at:
                latest_observed_at = max([value for value in (latest_observed_at, observed_at) if value], default=None)
            if available_at:
                latest_available_at = max([value for value in (latest_available_at, available_at) if value], default=None)
        quality_flags = sorted(
            {
                str(flag)
                for row in rows
                for flag in row.get("quality_flags") or []
                if str(flag)
            }
        )
        anomaly_flags = sorted(
            {
                str(flag)
                for row in rows
                for flag in (row.get("anomaly_flags") or [])
                if str(flag)
            }
            | set(detect_station_observation_anomalies(rows))
        )
        return {
            "schema_version": "polyweather_current_high_as_of.v1",
            "station_code": str(station_code or "").strip().upper(),
            "target_date": str(target_date or "").strip(),
            "replay_time": utc_iso(parse_utc(replay_time)),  # type: ignore[arg-type]
            "latest_observation_at": latest_observed_at,
            "latest_available_at": latest_available_at,
            "current_high_c": max(temperatures) if temperatures else None,
            "observation_count": len(rows),
            "source": str(settlement_source or "").strip().lower(),
            "quality_flags": quality_flags,
            "anomaly_flags": anomaly_flags,
            "no_lookahead": True,
        }
