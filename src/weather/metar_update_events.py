from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.weather.weather_observations import load_intraday_observations, observation_temperature_value
from src.weather.weather_sources import parse_utc


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_source_matches(row: Dict[str, Any], settlement_source: str) -> bool:
    source = _text(row.get("settlement_source") or row.get("source")).lower()
    wanted = _text(settlement_source).lower()
    return source == wanted or (wanted == "metar" and source.startswith("aviationweather_metar"))


def _visible_station_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    station_code: str,
    target_date: str,
    replay_time: str,
    settlement_source: str,
) -> List[Dict[str, Any]]:
    replay_dt = parse_utc(replay_time)
    if replay_dt is None:
        return []
    station = _text(station_code).upper()
    target = _text(target_date)
    visible: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _text(row.get("station_code")).upper() != station:
            continue
        if _text(row.get("target_date_local") or row.get("target_date")) != target:
            continue
        if _text(row.get("snapshot_type") or "observation") != "observation":
            continue
        if not _row_source_matches(row, settlement_source):
            continue
        available_at = parse_utc(row.get("available_at"))
        if available_at is None or available_at > replay_dt:
            continue
        visible.append(row)
    return sorted(visible, key=lambda item: (str(item.get("available_at") or ""), str(item.get("observed_at") or "")))


def detect_high_update_events(
    *,
    station_code: str,
    target_date: str,
    replay_time: str,
    observations: Optional[Iterable[Dict[str, Any]]] = None,
    observation_path: Optional[str | Path] = None,
    settlement_source: str = "metar",
    thresholds: Optional[Iterable[float]] = None,
) -> Dict[str, Any]:
    """Detect the latest visible METAR daily-high update without looking past replay_time."""

    source_rows = list(observations) if observations is not None else load_intraday_observations(observation_path or "")
    rows = _visible_station_rows(
        source_rows,
        station_code=station_code,
        target_date=target_date,
        replay_time=replay_time,
        settlement_source=settlement_source,
    )
    replay_dt = parse_utc(replay_time)
    if not rows or replay_dt is None:
        return {
            "station_code": _text(station_code).upper(),
            "target_date": _text(target_date),
            "replay_time": replay_time,
            "previous_high": None,
            "current_high": None,
            "high_changed": False,
            "crossed_thresholds": [],
            "previous_observation_at": None,
            "observation_at": None,
            "available_at": None,
            "update_age_seconds": None,
            "visible_observation_count": 0,
            "no_lookahead": True,
        }
    current_row = rows[-1]
    previous_rows = rows[:-1]
    previous_values = [observation_temperature_value(row) for row in previous_rows]
    previous_values = [float(value) for value in previous_values if value is not None]
    all_values = [observation_temperature_value(row) for row in rows]
    all_values = [float(value) for value in all_values if value is not None]
    previous_high = max(previous_values) if previous_values else None
    current_high = max(all_values) if all_values else None
    current_available = parse_utc(current_row.get("available_at"))
    update_age = int((replay_dt - current_available).total_seconds()) if current_available is not None else None
    crossed: List[Dict[str, Any]] = []
    if previous_high is not None and current_high is not None:
        for threshold in thresholds or []:
            threshold_value = _safe_float(threshold)
            if threshold_value is None:
                continue
            if previous_high < threshold_value <= current_high:
                crossed.append({"threshold": threshold_value, "direction": "ge_crossed"})
            if previous_high <= threshold_value < current_high:
                crossed.append({"threshold": threshold_value, "direction": "le_broken"})
    return {
        "station_code": _text(station_code).upper(),
        "target_date": _text(target_date),
        "replay_time": replay_time,
        "previous_high": previous_high,
        "current_high": current_high,
        "high_changed": bool(previous_high is not None and current_high is not None and current_high > previous_high),
        "crossed_thresholds": crossed,
        "previous_observation_at": previous_rows[-1].get("observed_at") if previous_rows else None,
        "observation_at": current_row.get("observed_at") or current_row.get("available_at"),
        "available_at": current_row.get("available_at"),
        "update_age_seconds": update_age,
        "visible_observation_count": len(rows),
        "no_lookahead": True,
    }
