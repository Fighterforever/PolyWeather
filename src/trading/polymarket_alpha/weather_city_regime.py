from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional

from src.data_collection.city_registry import CITY_REGISTRY
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_city_regime.v1"
DEFAULT_CITIES = ["beijing", "hong kong", "london", "moscow", "ankara", "istanbul"]


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _city_from_family_catalog(payload: Dict[str, Any]) -> List[str]:
    cities = set(DEFAULT_CITIES)
    for family in payload.get("families") or []:
        if isinstance(family, dict) and family.get("city"):
            cities.add(str(family["city"]).lower())
    return sorted(cities)


def _station_for_city(city: str) -> Dict[str, Any]:
    meta = CITY_REGISTRY.get(city) or {}
    return {
        "city": city,
        "city_display_name": meta.get("name") or city.title(),
        "station_code": meta.get("settlement_station_code") or meta.get("icao"),
        "settlement_source": meta.get("settlement_source") or ("metar" if meta.get("icao") else None),
        "risk_level": meta.get("risk_level"),
    }


def _observation_temp(row: Dict[str, Any]) -> Optional[float]:
    for key in ("daily_high", "official_final_value", "temperature_c", "temp_c", "temp"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def build_weather_city_regime(
    *,
    family_catalog: Dict[str, Any],
    observations: Iterable[Dict[str, Any]] = (),
    min_sample_count: int = 10,
) -> Dict[str, Any]:
    obs_by_station: Dict[str, List[float]] = defaultdict(list)
    for row in observations:
        if not isinstance(row, dict):
            continue
        station = str(row.get("station_code") or row.get("station") or row.get("icao") or "").upper()
        value = _observation_temp(row)
        if station and value is not None:
            obs_by_station[station].append(value)

    table: List[Dict[str, Any]] = []
    for city in _city_from_family_catalog(family_catalog):
        station = _station_for_city(city)
        code = str(station.get("station_code") or "").upper()
        samples = obs_by_station.get(code, [])
        sample_count = len(samples)
        if sample_count >= int(min_sample_count):
            std = pstdev(samples) if sample_count > 1 else 0.0
            volatility = "stable" if std < 2.0 else "normal" if std < 4.0 else "volatile"
            width = 2 if volatility == "stable" else 4 if volatility == "normal" else 6
            gap = None
        else:
            std = None
            volatility = "insufficient_data"
            width = None
            gap = "insufficient_station_history"
        table.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "city": city,
                "station_code": code or None,
                "settlement_source": station.get("settlement_source"),
                "sample_count": sample_count,
                "daily_high_mean": round(mean(samples), 6) if samples else None,
                "daily_high_std": round(float(std), 6) if std is not None else None,
                "intraday_peak_time_distribution": [],
                "station_city_bias_mean": None,
                "station_city_bias_std": None,
                "typical_range_width": width,
                "volatility_bucket": volatility,
                "recommended_range_width": width,
                "gap_reason": gap,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "city_profile_count": len(table),
        "sufficient_profile_count": len([row for row in table if row.get("volatility_bucket") != "insufficient_data"]),
        "gap_counts": _gap_counts(table),
        "city_regimes": table,
    }


def _gap_counts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get("gap_reason") or "none")] += 1
    return [{"reason": key, "count": value} for key, value in sorted(counts.items())]


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


__all__ = ["SCHEMA_VERSION", "build_weather_city_regime", "load_json", "load_jsonl", "write_json", "write_jsonl"]
