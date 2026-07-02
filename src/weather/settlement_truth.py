from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from src.weather.station_registry import StationSpec, station_for_city


OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION = "polyweather_official_temperature_value.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point_temp(point: Dict[str, Any]) -> Optional[float]:
    for key in ("temp", "value", "temperature", "temperature_c", "temperature_f"):
        value = _safe_float(point.get(key))
        if value is not None:
            return value
    return None


def _point_time(point: Dict[str, Any]) -> Optional[str]:
    for key in ("time", "observed_at", "observation_time", "obs_time"):
        value = _text(point.get(key))
        if value:
            return value
    return None


def daily_high_from_intraday_points(points: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    valid: List[Dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        temp = _point_temp(point)
        if temp is None:
            continue
        valid.append({"temp": float(temp), "time": _point_time(point), "raw": dict(point)})

    if not valid:
        return {
            "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
            "status": "missing_observation_points",
            "official_final_value": None,
            "observation_count": 0,
        }

    max_point = max(valid, key=lambda item: item["temp"])
    return {
        "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
        "status": "ready",
        "official_final_value": float(max_point["temp"]),
        "observation_count": len(valid),
        "max_observed_at": max_point.get("time"),
        "max_observation_payload": max_point.get("raw"),
    }


def _source_candidates(settlement_source: Optional[str]) -> List[str]:
    source = _text(settlement_source).lower()
    if not source:
        return []
    candidates = [source]
    if source == "metar":
        candidates.extend(["aviationweather", "noaa"])
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _default_repository() -> Any:
    from src.database.runtime_state import OfficialIntradayObservationRepository

    return OfficialIntradayObservationRepository()


def load_official_temperature_value(
    *,
    city: str,
    target_date: str,
    station: Optional[StationSpec] = None,
    station_code: Optional[str] = None,
    settlement_source: Optional[str] = None,
    source_candidates: Optional[Iterable[str]] = None,
    repository: Optional[Any] = None,
) -> Dict[str, Any]:
    """Load an official daily high from the intraday observation store.

    This is intentionally conservative: it only returns a final value when the
    station/date/source combination has stored official observations. Market
    resolution outcomes are never used to infer the value here.
    """

    normalized_city = _text(city).lower()
    normalized_date = _text(target_date)
    station = station or station_for_city(normalized_city)
    if station is None and not station_code:
        return {
            "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
            "status": "unsupported_city",
            "city": normalized_city,
            "target_date": normalized_date,
            "official_final_value": None,
        }

    resolved_station_code = _text(station_code or (station.station_code if station else ""))
    resolved_source = _text(settlement_source or (station.settlement_source if station else "")).lower()
    candidates = [
        _text(candidate).lower()
        for candidate in (source_candidates or _source_candidates(resolved_source))
        if _text(candidate)
    ]
    candidates = list(dict.fromkeys(candidates))
    if not normalized_date:
        return {
            "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
            "status": "missing_target_date",
            "city": normalized_city,
            "station_code": resolved_station_code,
            "official_final_value": None,
        }
    if not resolved_station_code:
        return {
            "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
            "status": "missing_station_code",
            "city": normalized_city,
            "target_date": normalized_date,
            "official_final_value": None,
        }
    if not candidates:
        return {
            "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
            "status": "missing_source_code",
            "city": normalized_city,
            "target_date": normalized_date,
            "station_code": resolved_station_code,
            "official_final_value": None,
        }

    repo = repository or _default_repository()
    attempted: List[Dict[str, Any]] = []
    for source_code in candidates:
        try:
            points = repo.load_points(
                source_code=source_code,
                station_code=resolved_station_code,
                target_date=normalized_date,
            )
        except Exception as exc:  # pragma: no cover - defensive around DB adapters
            attempted.append({"source_code": source_code, "status": "error", "error": str(exc)})
            continue
        derived = daily_high_from_intraday_points(points)
        attempted.append(
            {
                "source_code": source_code,
                "status": derived.get("status"),
                "observation_count": derived.get("observation_count", 0),
            }
        )
        if derived.get("status") == "ready":
            return {
                **derived,
                "city": normalized_city,
                "target_date": normalized_date,
                "station_code": resolved_station_code,
                "source_code": source_code,
                "source": "official_intraday_observation_store",
                "attempted_sources": attempted,
            }

    return {
        "schema_version": OFFICIAL_TEMPERATURE_VALUE_SCHEMA_VERSION,
        "status": "missing_observation_points",
        "city": normalized_city,
        "target_date": normalized_date,
        "station_code": resolved_station_code,
        "source_code": candidates[0],
        "source": "official_intraday_observation_store",
        "official_final_value": None,
        "attempted_sources": attempted,
    }
