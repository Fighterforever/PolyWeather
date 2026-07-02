from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


WEATHER_SNAPSHOT_SCHEMA_VERSION = "polyweather_station_weather_snapshot.v1"


def parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WeatherSnapshot:
    schema_version: str
    source: str
    snapshot_type: str
    station_code: str
    target_date: str
    available_at: str
    payload: Dict[str, Any]
    issued_at: Optional[str] = None
    observed_at: Optional[str] = None
    model: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_weather_snapshot(
    *,
    source: str,
    snapshot_type: str,
    station_code: str,
    target_date: str,
    available_at: str,
    payload: Dict[str, Any],
    issued_at: Optional[str] = None,
    observed_at: Optional[str] = None,
    model: Optional[str] = None,
) -> WeatherSnapshot:
    if parse_utc(available_at) is None:
        raise ValueError("available_at must be an ISO UTC timestamp")
    if snapshot_type not in {"forecast", "observation", "settlement"}:
        raise ValueError("snapshot_type must be forecast, observation, or settlement")
    if not station_code:
        raise ValueError("station_code is required")
    if not target_date:
        raise ValueError("target_date is required")
    return WeatherSnapshot(
        schema_version=WEATHER_SNAPSHOT_SCHEMA_VERSION,
        source=str(source or "unknown"),
        snapshot_type=snapshot_type,
        station_code=str(station_code),
        target_date=str(target_date),
        available_at=utc_iso(parse_utc(available_at)),  # type: ignore[arg-type]
        payload=dict(payload or {}),
        issued_at=utc_iso(parse_utc(issued_at)) if parse_utc(issued_at) else None,
        observed_at=utc_iso(parse_utc(observed_at)) if parse_utc(observed_at) else None,
        model=str(model) if model else None,
    )


def snapshots_available_for_replay(
    snapshots: Iterable[WeatherSnapshot | Dict[str, Any]],
    *,
    replay_time: str,
) -> List[Dict[str, Any]]:
    replay_dt = parse_utc(replay_time)
    if replay_dt is None:
        raise ValueError("replay_time must be an ISO UTC timestamp")
    visible: List[Dict[str, Any]] = []
    for snapshot in snapshots:
        row = snapshot.to_dict() if isinstance(snapshot, WeatherSnapshot) else dict(snapshot)
        available_dt = parse_utc(row.get("available_at"))
        if available_dt is not None and available_dt <= replay_dt:
            visible.append(row)
    return sorted(visible, key=lambda row: str(row.get("available_at") or ""))
