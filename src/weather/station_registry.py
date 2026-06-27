from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from src.data_collection.city_registry import CITY_REGISTRY


STATION_SPEC_SCHEMA_VERSION = "polyweather_station_spec.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _timezone_from_offset(offset_seconds: Any) -> Optional[str]:
    try:
        total_seconds = int(offset_seconds)
    except (TypeError, ValueError):
        return None
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


@dataclass(frozen=True)
class StationSpec:
    schema_version: str
    city: str
    city_display_name: str
    station_code: str
    station_label: str
    settlement_source: str
    timezone: str
    lat: Optional[float]
    lon: Optional[float]
    use_fahrenheit: bool
    settlement_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def station_for_city(city: str) -> Optional[StationSpec]:
    city_key = _text(city).lower()
    meta = CITY_REGISTRY.get(city_key) or {}
    if not meta:
        return None
    station_code = _text(meta.get("settlement_station_code") or meta.get("icao"))
    station_label = _text(meta.get("settlement_station_label") or meta.get("airport_name") or station_code)
    settlement_source = _text(meta.get("settlement_source") or ("metar" if station_code else ""))
    timezone_name = _timezone_from_offset(meta.get("tz_offset"))
    if not station_code or not station_label or not settlement_source or not timezone_name:
        return None
    return StationSpec(
        schema_version=STATION_SPEC_SCHEMA_VERSION,
        city=city_key,
        city_display_name=_text(meta.get("name") or city_key),
        station_code=station_code,
        station_label=station_label,
        settlement_source=settlement_source,
        timezone=timezone_name,
        lat=float(meta["lat"]) if meta.get("lat") is not None else None,
        lon=float(meta["lon"]) if meta.get("lon") is not None else None,
        use_fahrenheit=bool(meta.get("use_fahrenheit")),
        settlement_url=_text(meta.get("settlement_url")) or None,
    )


def station_registry_snapshot() -> Dict[str, Dict[str, Any]]:
    return {
        city: spec.to_dict()
        for city in CITY_REGISTRY
        for spec in [station_for_city(city)]
        if spec is not None
    }
