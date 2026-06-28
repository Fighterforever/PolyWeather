from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.data_collection.city_registry import CITY_REGISTRY
from src.weather.weather_observations import (
    INTRADAY_OBSERVATION_SCHEMA_VERSION,
    detect_station_observation_anomalies,
)
from src.weather.weather_sources import parse_utc, utc_iso


SOURCE_NAME = "aviationweather_metar_recent_72h"
HISTORY_SOURCE_NAME = "aviationweather_metar_history"
SUPPORTED_METAR_STATIONS = {"LTAC", "UUWW", "EGLC"}
AVIATIONWEATHER_METAR_URL = "https://aviationweather.gov/api/data/metar"
DEFAULT_USER_AGENT = "PolyWeatherResearch/1.0 paper-only aviationweather-metar-backfill"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _station_timezone_map() -> Dict[str, str]:
    rows: Dict[str, str] = {}
    for meta in CITY_REGISTRY.values():
        code = str(meta.get("settlement_station_code") or meta.get("icao") or "").strip().upper()
        if not code:
            continue
        try:
            offset = int(meta.get("tz_offset"))
        except (TypeError, ValueError):
            continue
        sign = "+" if offset >= 0 else "-"
        offset = abs(offset)
        hours, remainder = divmod(offset, 3600)
        minutes = remainder // 60
        rows[code] = f"UTC{sign}{hours:02d}:{minutes:02d}"
    return rows


def _timezone_from_text(value: str) -> timezone:
    text = str(value or "UTC").strip().upper()
    if text in {"UTC", "Z"}:
        return timezone.utc
    sign = 1
    if "+" in text:
        _, raw = text.split("+", 1)
    elif "-" in text:
        _, raw = text.split("-", 1)
        sign = -1
    else:
        return timezone.utc
    hours_text, _, minutes_text = raw.partition(":")
    try:
        hours = int(hours_text or 0)
        minutes = int(minutes_text or 0)
    except ValueError:
        return timezone.utc
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _target_date_local(observed_at: str, timezone_name: str) -> Optional[str]:
    parsed = parse_utc(observed_at)
    if parsed is None:
        return None
    return parsed.astimezone(_timezone_from_text(timezone_name)).date().isoformat()


def _parse_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000.0
        return utc_iso(datetime.fromtimestamp(number, timezone.utc))
    parsed = parse_utc(value)
    return utc_iso(parsed) if parsed else None


def build_aviationweather_url(station_codes: Iterable[str], *, hours: int = 72) -> str:
    ids = ",".join(sorted({str(code).strip().upper() for code in station_codes if str(code).strip()}))
    query = urllib.parse.urlencode({"ids": ids, "format": "json", "hours": max(1, int(hours))})
    return f"{AVIATIONWEATHER_METAR_URL}?{query}"


def fetch_aviationweather_metars(
    station_codes: Iterable[str],
    *,
    hours: int = 72,
    timeout: int = 20,
    user_agent: str = DEFAULT_USER_AGENT,
) -> List[Dict[str, Any]]:
    url = build_aviationweather_url(station_codes, hours=hours)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("aviationweather METAR API returned non-list payload")
    return [row for row in payload if isinstance(row, dict)]


def parse_aviationweather_metar_row(
    row: Dict[str, Any],
    *,
    fetched_at: str,
    timezone_by_station: Optional[Dict[str, str]] = None,
    source: str = SOURCE_NAME,
    conservative_available_delay_minutes: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    station = str(row.get("icaoId") or row.get("station_id") or row.get("stationCode") or "").strip().upper()
    if not station:
        return None
    timezone_by_station = timezone_by_station or _station_timezone_map()
    timezone_name = timezone_by_station.get(station, "UTC")
    observed_at = _parse_time(row.get("reportTime") or row.get("obsTime") or row.get("observation_time"))
    receipt_at = _parse_time(row.get("receiptTime") or row.get("available_at"))
    fetched_dt = parse_utc(fetched_at)
    available_at = receipt_at or observed_at or fetched_at
    if receipt_at is None and observed_at and conservative_available_delay_minutes is not None:
        observed_dt = parse_utc(observed_at)
        if observed_dt is not None:
            available_at = utc_iso(observed_dt + timedelta(minutes=float(conservative_available_delay_minutes)))
    available_dt = parse_utc(available_at)
    quality_flags: List[str] = []
    if fetched_dt is not None and available_dt is not None and available_dt > fetched_dt:
        available_at = utc_iso(fetched_dt)
        quality_flags.append("available_at_clamped_to_fetched_at")
    temperature = _safe_float(row.get("temp") or row.get("temp_c") or row.get("temperature_c"))
    if temperature is None:
        quality_flags.append("missing_temperature_c")
    if observed_at is None:
        quality_flags.append("missing_observed_at")
    source_latency_seconds = None
    if fetched_dt is not None and parse_utc(available_at) is not None:
        source_latency_seconds = max(0, int((fetched_dt - parse_utc(available_at)).total_seconds()))  # type: ignore[union-attr]
    target_date = _target_date_local(observed_at or available_at, timezone_name) if (observed_at or available_at) else None
    payload = {
        "schema_version": INTRADAY_OBSERVATION_SCHEMA_VERSION,
        "station_code": station,
        "settlement_source": "metar",
        "source": source,
        "snapshot_type": "observation",
        "observed_at": observed_at,
        "available_at": available_at,
        "fetched_at": fetched_at,
        "temperature_c": temperature,
        "raw_temperature": row.get("temp"),
        "raw_metar": row.get("rawOb") or row.get("raw_text"),
        "source_payload_ref": None,
        "target_date_local": target_date,
        "target_date": target_date,
        "timezone": timezone_name,
        "source_latency_seconds": source_latency_seconds,
        "available_at_semantics": (
            "receipt_time"
            if receipt_at
            else (
                "observed_at_plus_conservative_delay"
                if conservative_available_delay_minutes is not None and observed_at
                else "observed_at_or_fetch_time_fallback"
            )
        ),
        "conservative_available_delay_minutes": conservative_available_delay_minutes,
        "quality_flags": sorted(set(quality_flags)),
        "anomaly_flags": [],
        "paper_only": True,
        "counts_for_live_gate": False,
        "payload": {
            "temperature_c": temperature,
            "official_source_flag": True,
            "raw": row,
        },
    }
    return payload


def build_intraday_observations_from_api_rows(
    api_rows: Iterable[Dict[str, Any]],
    *,
    fetched_at: str,
    station_codes: Iterable[str],
    source: str = SOURCE_NAME,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    conservative_available_delay_minutes: Optional[float] = None,
) -> Dict[str, Any]:
    station_set = {str(code).strip().upper() for code in station_codes if str(code).strip()}
    observations = [
        parsed
        for row in api_rows
        for parsed in [
            parse_aviationweather_metar_row(
                row,
                fetched_at=fetched_at,
                source=source,
                conservative_available_delay_minutes=conservative_available_delay_minutes,
            )
        ]
        if parsed is not None and parsed.get("station_code") in station_set
    ]
    if start_date or end_date:
        observations = [
            row
            for row in observations
            if (not start_date or str(row.get("target_date_local") or "") >= str(start_date))
            and (not end_date or str(row.get("target_date_local") or "") <= str(end_date))
        ]
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for row in observations:
        grouped.setdefault((str(row.get("station_code")), str(row.get("target_date_local"))), []).append(row)
    for rows in grouped.values():
        anomalies = detect_station_observation_anomalies(rows)
        if anomalies:
            for row in rows:
                row["anomaly_flags"] = sorted(set(row.get("anomaly_flags") or []) | set(anomalies))
    found = {str(row.get("station_code")) for row in observations}
    gaps = [
        {
            "station_code": station,
            "settlement_source": "metar",
            "source": source,
            "gap_reason": "missing_metar_rows_from_source",
        }
        for station in sorted(station_set - found)
    ]
    return {
        "schema_version": "polyweather_metar_intraday_collection.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fetched_at": fetched_at,
        "source": SOURCE_NAME,
        "row_source": source,
        "start_date": start_date,
        "end_date": end_date,
        "station_codes": sorted(station_set),
        "observations": observations,
        "gaps": gaps,
        "observation_count": len(observations),
        "station_count": len(found),
    }


def collect_metar_intraday_observations(
    *,
    station_codes: Iterable[str],
    fetched_at: Optional[str] = None,
    api_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    fetched_at = fetched_at or utc_iso(datetime.now(timezone.utc))
    requested = [str(code).strip().upper() for code in station_codes if str(code).strip()]
    unsupported = sorted(set(requested) - SUPPORTED_METAR_STATIONS)
    rows = list(api_rows) if api_rows is not None else fetch_aviationweather_metars(requested)
    report = build_intraday_observations_from_api_rows(rows, fetched_at=fetched_at, station_codes=requested)
    report["unsupported_station_codes"] = unsupported
    if unsupported:
        report["gaps"].extend(
            {
                "station_code": station,
                "settlement_source": "metar",
                "source": SOURCE_NAME,
                "gap_reason": "unsupported_station_code",
            }
            for station in unsupported
        )
    return report


def _parse_date(value: str) -> date:
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _utc_hours_for_date_range(start_date: str, end_date: str, *, now: Optional[datetime] = None) -> int:
    start = datetime.combine(_parse_date(start_date), time.min, tzinfo=timezone.utc)
    end = datetime.combine(_parse_date(end_date), time.max.replace(microsecond=0), tzinfo=timezone.utc)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if end > now:
        end = now
    hours = max(1, int((end - start).total_seconds() // 3600) + 2)
    return min(360, hours)


def collect_metar_intraday_history(
    *,
    station_codes: Iterable[str],
    start_date: str,
    end_date: str,
    fetched_at: Optional[str] = None,
    api_rows: Optional[Iterable[Dict[str, Any]]] = None,
    timeout: int = 20,
    user_agent: str = DEFAULT_USER_AGENT,
    conservative_available_delay_minutes: float = 10.0,
) -> Dict[str, Any]:
    """Backfill recent METAR timeline using AviationWeather's recent-data API.

    AviationWeather's public data API is recent-history oriented. We request the
    smallest UTC hours window that covers the requested dates, cap it at 15 days,
    then filter by each station's local target date. Rows remain paper-only and
    never count for live gates.
    """

    fetched_at = fetched_at or utc_iso(datetime.now(timezone.utc))
    requested = [str(code).strip().upper() for code in station_codes if str(code).strip()]
    hours = _utc_hours_for_date_range(start_date, end_date, now=parse_utc(fetched_at))
    rows = list(api_rows) if api_rows is not None else fetch_aviationweather_metars(
        requested,
        hours=hours,
        timeout=timeout,
        user_agent=user_agent,
    )
    report = build_intraday_observations_from_api_rows(
        rows,
        fetched_at=fetched_at,
        station_codes=requested,
        source=HISTORY_SOURCE_NAME,
        start_date=start_date,
        end_date=end_date,
        conservative_available_delay_minutes=conservative_available_delay_minutes,
    )
    report["schema_version"] = "polyweather_metar_intraday_history_backfill.v1"
    report["source"] = HISTORY_SOURCE_NAME
    report["requested_hours"] = hours
    report["user_agent"] = user_agent
    report["conservative_available_delay_minutes"] = conservative_available_delay_minutes
    return report
