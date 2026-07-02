from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.data_collection.city_registry import CITY_REGISTRY


SCHEMA_VERSION = "polyweather_open_meteo_historical_forecast.v1"
OPEN_METEO_HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"


def _text(value: Any) -> str:
    return str(value or "").strip()


def station_coordinates() -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for city, meta in CITY_REGISTRY.items():
        station = _text(meta.get("settlement_station_code") or meta.get("icao")).upper()
        if not station:
            continue
        rows[station] = {
            "city": city,
            "station_code": station,
            "lat": meta.get("lat"),
            "lon": meta.get("lon"),
            "timezone": meta.get("tz_offset"),
        }
    return rows


def build_open_meteo_historical_forecast_url(
    *,
    latitude: float,
    longitude: float,
    target_date: str,
    model: str = "gfs_seamless",
) -> str:
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": target_date,
            "end_date": target_date,
            "hourly": "temperature_2m",
            "models": model,
            "timezone": "UTC",
        }
    )
    return f"{OPEN_METEO_HISTORICAL_FORECAST_URL}?{query}"


def fetch_open_meteo_historical_forecast(
    *,
    latitude: float,
    longitude: float,
    target_date: str,
    model: str = "gfs_seamless",
    timeout: int = 20,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        build_open_meteo_historical_forecast_url(
            latitude=latitude,
            longitude=longitude,
            target_date=target_date,
            model=model,
        ),
        headers={"User-Agent": "PolyWeatherResearch/1.0 paper-only open-meteo-backfill"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo historical forecast returned non-object payload")
    return payload


def forecast_row_from_open_meteo_payload(
    payload: Dict[str, Any],
    *,
    station_code: str,
    latitude: float,
    longitude: float,
    target_date: str,
    model: str,
) -> Dict[str, Any]:
    hourly = payload.get("hourly") if isinstance(payload.get("hourly"), dict) else {}
    times = hourly.get("time") if isinstance(hourly.get("time"), list) else []
    values = hourly.get("temperature_2m") if isinstance(hourly.get("temperature_2m"), list) else []
    temperatures = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) or _text(value)
    ]
    daily_high = max(temperatures) if temperatures else None
    available_at = datetime.combine(
        datetime.strptime(target_date, "%Y-%m-%d").date(),
        time.min,
        tzinfo=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "station_code": _text(station_code).upper(),
        "lat": float(latitude),
        "lon": float(longitude),
        "target_date": target_date,
        "forecast_generated_at": None,
        "model_run_time": None,
        "available_at": available_at,
        "available_at_semantics": "target_date_0000z_reconstructed_historical_forecast_no_model_run_time",
        "model": model,
        "predicted_hourly_temperature": [
            {"time": str(timestamp), "temperature_c": temperature}
            for timestamp, temperature in zip(times, temperatures)
        ],
        "predicted_daily_high": daily_high,
        "source": "open_meteo_historical_forecast",
        "no_lookahead": True,
        "source_payload_ref": {
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "generationtime_ms": payload.get("generationtime_ms"),
        },
    }


def backfill_open_meteo_historical_forecasts(
    *,
    station_dates: Iterable[Dict[str, Any]],
    models: Iterable[str] = ("gfs_seamless",),
    timeout: int = 20,
) -> Dict[str, Any]:
    coord_by_station = station_coordinates()
    rows: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in station_dates:
        station = _text(item.get("station_code")).upper()
        target_date = _text(item.get("target_date"))
        if not station or not target_date:
            continue
        coords = coord_by_station.get(station)
        if not coords or coords.get("lat") is None or coords.get("lon") is None:
            gaps.append({"station_code": station, "target_date": target_date, "gap_reason": "missing_station_coordinates"})
            continue
        for model in models:
            key = (station, target_date, str(model))
            if key in seen:
                continue
            seen.add(key)
            try:
                payload = fetch_open_meteo_historical_forecast(
                    latitude=float(coords["lat"]),
                    longitude=float(coords["lon"]),
                    target_date=target_date,
                    model=str(model),
                    timeout=timeout,
                )
                rows.append(
                    forecast_row_from_open_meteo_payload(
                        payload,
                        station_code=station,
                        latitude=float(coords["lat"]),
                        longitude=float(coords["lon"]),
                        target_date=target_date,
                        model=str(model),
                    )
                )
            except Exception as exc:
                gaps.append(
                    {
                        "station_code": station,
                        "target_date": target_date,
                        "model": str(model),
                        "gap_reason": "source_fetch_error",
                        "gap_detail": str(exc),
                    }
                )
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "forecast_count": len(rows),
        "gap_count": len(gaps),
        "rows": rows,
        "gaps": gaps,
    }


def write_forecast_artifacts(report: Dict[str, Any], *, output_path: str | Path, manifest_path: str | Path) -> Dict[str, Any]:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [row for row in report.get("rows") or [] if isinstance(row, dict)]
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "schema_version": "polyweather_open_meteo_historical_forecast_manifest.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "output_path": str(output_path),
        "forecast_count": len(rows),
        "station_count": len({row.get("station_code") for row in rows if row.get("station_code")}),
        "gap_count": len(report.get("gaps") or []),
        "gaps": report.get("gaps") or [],
    }
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest
