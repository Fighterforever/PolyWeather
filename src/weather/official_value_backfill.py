from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.data_collection.city_registry import CITY_REGISTRY
from src.weather.settlement_truth import daily_high_from_intraday_points
from src.weather.settlement_truth import load_official_temperature_value


OFFICIAL_VALUE_BACKFILL_SCHEMA_VERSION = "polyweather_official_value_backfill.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_key(record: Dict[str, Any]) -> str:
    market_slug = _text(record.get("market_slug") or record.get("slug"))
    if market_slug:
        return f"slug:{market_slug}"
    market_id = _text(record.get("market_id"))
    if market_id:
        return f"id:{market_id}"
    return ""


def _market_keys(record: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    market_slug = _text(record.get("market_slug") or record.get("slug"))
    if market_slug:
        keys.append(f"slug:{market_slug}")
    market_id = _text(record.get("market_id"))
    if market_id:
        keys.append(f"id:{market_id}")
    return keys


def _record_settlement_spec(record: Dict[str, Any]) -> Dict[str, Any]:
    spec = record.get("settlement_spec")
    return spec if isinstance(spec, dict) else {}


def _record_city(record: Dict[str, Any]) -> str:
    parsed = record.get("parsed_temperature_spec") if isinstance(record.get("parsed_temperature_spec"), dict) else {}
    spec = _record_settlement_spec(record)
    return _text(record.get("city") or parsed.get("city") or spec.get("city")).lower()


def _record_target_date(record: Dict[str, Any]) -> str:
    parsed = record.get("parsed_temperature_spec") if isinstance(record.get("parsed_temperature_spec"), dict) else {}
    spec = _record_settlement_spec(record)
    return _text(record.get("target_date") or parsed.get("target_date") or spec.get("target_date"))


def _record_unit(record: Dict[str, Any]) -> str:
    parsed = record.get("parsed_temperature_spec") if isinstance(record.get("parsed_temperature_spec"), dict) else {}
    spec = _record_settlement_spec(record)
    return (_text(parsed.get("unit") or spec.get("unit")) or "C").upper()


def _record_source(record: Dict[str, Any]) -> str:
    spec = _record_settlement_spec(record)
    return _text(spec.get("settlement_source") or record.get("settlement_source")).lower()


def _record_station_code(record: Dict[str, Any]) -> str:
    spec = _record_settlement_spec(record)
    return _text(spec.get("station_code") or record.get("settlement_station_code")).upper()


def _city_utc_offset(city: str) -> int:
    meta = CITY_REGISTRY.get(city) or {}
    try:
        return int(meta.get("tz_offset") or 0)
    except (TypeError, ValueError):
        return 0


def _city_uses_fahrenheit(city: str, unit: str) -> bool:
    if unit.upper() == "F":
        return True
    meta = CITY_REGISTRY.get(city) or {}
    return bool(meta.get("use_fahrenheit") or meta.get("f"))


def _temp_for_unit(temp_c: float, unit: str) -> float:
    if unit.upper() == "F":
        return round(float(temp_c) * 9.0 / 5.0 + 32.0, 1)
    return round(float(temp_c), 1)


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _default_weather_collector() -> Any:
    from src.data_collection.weather_sources import WeatherDataCollector

    return WeatherDataCollector({"weather": {}})


def _base_supplement(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": OFFICIAL_VALUE_BACKFILL_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "diagnostic_only": True,
        "record_id": record.get("record_id"),
        "market_id": record.get("market_id"),
        "market_slug": record.get("market_slug"),
        "event_slug": record.get("event_slug"),
        "question": record.get("question"),
        "city": _record_city(record),
        "target_date": _record_target_date(record),
        "station_code": _record_station_code(record),
        "settlement_source": _record_source(record),
        "unit": _record_unit(record),
        "official_final_value": None,
    }


def _ready_supplement(record: Dict[str, Any], *, source: Dict[str, Any], method: str) -> Dict[str, Any]:
    supplement = _base_supplement(record)
    supplement.update(
        {
            "status": "ready",
            "official_final_value": _safe_float(source.get("official_final_value")),
            "official_final_value_source": source.get("source"),
            "official_final_value_source_code": source.get("source_code"),
            "official_final_value_station_code": source.get("station_code"),
            "official_final_value_observed_at": source.get("max_observed_at"),
            "official_final_value_observation_count": source.get("observation_count"),
            "retrieval_method": method,
        }
    )
    return supplement


def _gap_supplement(record: Dict[str, Any], reason: str, *, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    supplement = _base_supplement(record)
    supplement.update(
        {
            "status": "gap",
            "gap_reason": reason,
            "gap_detail": dict(detail or {}),
        }
    )
    return supplement


def _fetch_wunderground_value(
    record: Dict[str, Any],
    *,
    collector: Optional[Any] = None,
    allow_wunderground_proxy: bool = False,
) -> Dict[str, Any]:
    city = _record_city(record)
    target_date = _record_target_date(record)
    source = _record_source(record)
    unit = _record_unit(record)
    station_code = _record_station_code(record)
    if source != "wunderground" and not allow_wunderground_proxy:
        return _gap_supplement(
            record,
            "external_source_not_enabled",
            detail={"settlement_source": source or None},
        )
    if not city or not target_date:
        return _gap_supplement(record, "missing_city_or_target_date")

    wx = collector or _default_weather_collector()
    try:
        payload = wx.fetch_wunderground_historical(
            city,
            use_fahrenheit=_city_uses_fahrenheit(city, unit),
            utc_offset=_city_utc_offset(city),
            local_date=target_date,
        )
    except Exception as exc:  # pragma: no cover - defensive around external source adapters
        return _gap_supplement(record, "wunderground_fetch_error", detail={"error": str(exc)})
    if not isinstance(payload, dict):
        return _gap_supplement(record, "wunderground_no_payload")

    payload_station = _text(payload.get("station_code")).upper()
    if station_code and payload_station and station_code != payload_station:
        return _gap_supplement(
            record,
            "station_mismatch",
            detail={"expected_station_code": station_code, "payload_station_code": payload_station},
        )
    daily_high = _safe_float(payload.get("daily_high") if payload.get("daily_high") is not None else payload.get("max_so_far"))
    if daily_high is None:
        return _gap_supplement(record, "wunderground_missing_daily_high")
    return _ready_supplement(
        record,
        source={
            "official_final_value": daily_high,
            "source": "wunderground_historical",
            "source_code": "wunderground",
            "station_code": payload_station or station_code,
            "max_observed_at": payload.get("max_temp_time"),
            "observation_count": payload.get("observation_count"),
        },
        method="wunderground_historical",
    )


def _fetch_station_recent_value(
    record: Dict[str, Any],
    *,
    collector: Optional[Any] = None,
    method: str,
    source_name: str,
    source_code: str,
) -> Dict[str, Any]:
    station_code = _record_station_code(record)
    city = _record_city(record)
    target_date = _record_target_date(record)
    unit = _record_unit(record)
    if not station_code or not target_date:
        return _gap_supplement(record, "missing_station_or_target_date")

    wx = collector or _default_weather_collector()
    cache_key = (source_code, station_code, target_date, unit)
    cache = getattr(wx, "_official_value_metar_recent_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            setattr(wx, "_official_value_metar_recent_cache", cache)
        except Exception:
            pass
    if cache_key in cache:
        cached = cache[cache_key]
        if isinstance(cached, dict) and cached.get("status") == "ready":
            return _ready_supplement(
                record,
                source=dict(cached.get("source") or {}),
                method=str(cached.get("method") or method),
            )
        if isinstance(cached, dict):
            return _gap_supplement(
                record,
                str(cached.get("reason") or f"{source_code}_recent_gap"),
                detail=dict(cached.get("detail") or {}),
            )

    try:
        response = wx._http_get(
            "https://aviationweather.gov/api/data/metar",
            params={
                "ids": station_code,
                "format": "json",
                "hours": 72,
            },
            timeout=getattr(wx, "metar_timeout_sec", getattr(wx, "timeout", 8)),
        )
        response.raise_for_status()
        rows = response.json() if response.content else []
    except Exception as exc:  # pragma: no cover - defensive around external HTTP
        reason = f"{source_code}_recent_fetch_error"
        cache[cache_key] = {"status": "gap", "reason": reason, "detail": {"error": str(exc)}}
        return _gap_supplement(record, reason, detail={"error": str(exc)})
    if not isinstance(rows, list) or not rows:
        reason = f"{source_code}_recent_no_payload"
        cache[cache_key] = {"status": "gap", "reason": reason}
        return _gap_supplement(record, reason)

    utc_offset = _city_utc_offset(city)
    points: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = _text(row.get("icaoId") or row.get("station_id")).upper()
        if station and station != station_code:
            continue
        temp_c = _safe_float(row.get("temp"))
        if temp_c is None:
            continue
        reported_at = _parse_utc_datetime(row.get("reportTime"))
        if reported_at is None and row.get("obsTime") is not None:
            try:
                reported_at = datetime.fromtimestamp(int(row.get("obsTime")), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                reported_at = None
        if reported_at is None:
            continue
        local_dt = reported_at + timedelta(seconds=utc_offset)
        if local_dt.strftime("%Y-%m-%d") != target_date:
            continue
        points.append(
            {
                "time": local_dt.strftime("%H:%M"),
                "temp": _temp_for_unit(float(temp_c), unit),
                "reported_at": reported_at.isoformat().replace("+00:00", "Z"),
                "raw": row,
            }
        )

    derived = daily_high_from_intraday_points(points)
    if derived.get("status") != "ready" or derived.get("official_final_value") is None:
        detail = {"station_code": station_code, "target_date": target_date, "row_count": len(rows)}
        cache[cache_key] = {
            "status": "gap",
            "reason": f"{source_code}_recent_missing_target_date_points",
            "detail": detail,
        }
        return _gap_supplement(
            record,
            f"{source_code}_recent_missing_target_date_points",
            detail=detail,
        )
    source = {
        "official_final_value": derived.get("official_final_value"),
        "source": source_name,
        "source_code": source_code,
        "station_code": station_code,
        "max_observed_at": derived.get("max_observed_at"),
        "observation_count": derived.get("observation_count"),
    }
    cache[cache_key] = {
        "status": "ready",
        "source": source,
        "method": method,
    }
    return _ready_supplement(
        record,
        source=source,
        method=method,
    )


def _fetch_metar_recent_value(
    record: Dict[str, Any],
    *,
    collector: Optional[Any] = None,
) -> Dict[str, Any]:
    return _fetch_station_recent_value(
        record,
        collector=collector,
        method="aviationweather_metar_recent_72h",
        source_name="aviationweather_metar_recent",
        source_code="metar",
    )


def _fetch_noaa_station_recent_value(
    record: Dict[str, Any],
    *,
    collector: Optional[Any] = None,
) -> Dict[str, Any]:
    return _fetch_station_recent_value(
        record,
        collector=collector,
        method="aviationweather_noaa_station_recent_72h",
        source_name="aviationweather_noaa_station_recent",
        source_code="noaa_station_observation",
    )


def _add_count(mapping: Dict[str, int], key: Any) -> None:
    value = _text(key) or "unknown"
    mapping[value] = mapping.get(value, 0) + 1


def _count_rows(mapping: Dict[str, int], key_name: str) -> List[Dict[str, Any]]:
    return [
        {key_name: key, "count": count}
        for key, count in sorted(mapping.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _external_method_for_source(source: str) -> Optional[str]:
    normalized = _text(source).lower()
    if normalized == "metar":
        return "aviationweather_metar_recent_72h"
    if normalized == "noaa":
        return "aviationweather_noaa_station_recent_72h"
    if normalized == "wunderground":
        return "wunderground_historical"
    return None


def _unsupported_source_group_fields(source: str) -> Dict[str, Any]:
    if _external_method_for_source(source) is not None:
        return {
            "group_state": "official_source_supported",
            "calibration_excluded_reason": None,
        }
    return {
        "group_state": "official_source_unsupported",
        "calibration_excluded_reason": "unsupported_official_source_adapter",
    }


def _official_observation_backfill_plan(
    supplements: Iterable[Dict[str, Any]],
    *,
    max_requests: int = 20,
) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, Any]] = {}
    skipped_gap_count = 0
    for supplement in supplements:
        if not isinstance(supplement, dict) or supplement.get("status") == "ready":
            continue
        city = _text(supplement.get("city")).lower()
        target_date = _text(supplement.get("target_date"))
        station_code = _text(supplement.get("station_code")).upper()
        settlement_source = _text(supplement.get("settlement_source")).lower()
        unit = (_text(supplement.get("unit")) or "C").upper()
        if not target_date or not station_code or not settlement_source:
            skipped_gap_count += 1
            continue
        key = "|".join([settlement_source, station_code, target_date, unit])
        request = grouped.setdefault(
            key,
            {
                "settlement_source": settlement_source,
                "station_code": station_code,
                "target_date": target_date,
                "unit": unit,
                "cities": [],
                "record_count": 0,
                "gap_reasons": {},
                "market_slug_samples": [],
                "supported_external_method": _external_method_for_source(settlement_source),
                "supported": _external_method_for_source(settlement_source) is not None,
                **_unsupported_source_group_fields(settlement_source),
            },
        )
        if city and city not in request["cities"]:
            request["cities"].append(city)
        request["record_count"] += 1
        _add_count(request["gap_reasons"], supplement.get("gap_reason"))
        market_slug = _text(supplement.get("market_slug"))
        if market_slug and len(request["market_slug_samples"]) < 5:
            request["market_slug_samples"].append(market_slug)

    requests: List[Dict[str, Any]] = []
    by_source: Dict[str, int] = {}
    by_gap_reason: Dict[str, int] = {}
    for request in grouped.values():
        _add_count(by_source, request.get("settlement_source"))
        for reason, count in request["gap_reasons"].items():
            by_gap_reason[reason] = by_gap_reason.get(reason, 0) + int(count)
        request["cities"] = sorted(request["cities"])
        request["gap_reasons"] = _count_rows(request["gap_reasons"], "reason")
        request["counts_for_live_gate"] = False if request.get("supported") is False else True
        request["next_action"] = (
            "Fetch the supported external official source, then persist the station/date observations into the official observation store."
            if request.get("supported_external_method")
            else "Populate the local official observation store for this station/date/source before using settlement calibration."
        )
        requests.append(request)
    requests.sort(
        key=lambda row: (
            -int(row.get("record_count") or 0),
            str(row.get("settlement_source") or ""),
            str(row.get("station_code") or ""),
            str(row.get("target_date") or ""),
        )
    )
    return {
        "schema_version": "polyweather_official_observation_backfill_plan.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "request_count": len(requests),
        "returned_request_count": min(len(requests), max(0, int(max_requests))),
        "truncated": len(requests) > max(0, int(max_requests)),
        "records_covered_count": sum(int(row.get("record_count") or 0) for row in requests),
        "skipped_gap_count": skipped_gap_count,
        "by_settlement_source": _count_rows(by_source, "settlement_source"),
        "by_gap_reason": _count_rows(by_gap_reason, "reason"),
        "requests": requests[: max(0, int(max_requests))],
    }


def build_official_observation_request_plan(
    records: Iterable[Dict[str, Any]],
    *,
    expected_reuse_market_count: Optional[int] = None,
    max_requests: int = 20,
) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, Any]] = {}
    input_count = 0
    skipped_record_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        input_count += 1
        city = _record_city(record)
        target_date = _record_target_date(record)
        station_code = _record_station_code(record)
        settlement_source = _record_source(record)
        unit = _record_unit(record)
        if not target_date or not station_code or not settlement_source:
            skipped_record_count += 1
            continue
        key = "|".join([settlement_source, station_code, target_date, unit])
        method = _external_method_for_source(settlement_source)
        request = grouped.setdefault(
            key,
            {
                "settlement_source": settlement_source,
                "station_code": station_code,
                "target_date": target_date,
                "unit": unit,
                "cities": [],
                "market_count": 0,
                "token_count": 0,
                "market_slug_samples": [],
                "supported_external_method": method,
                "supported": method is not None,
                "gap_reason": None if method is not None else "unsupported_source_adapter",
                **_unsupported_source_group_fields(settlement_source),
            },
        )
        if city and city not in request["cities"]:
            request["cities"].append(city)
        request["market_count"] += 1
        request["token_count"] += 1
        market_slug = _text(record.get("market_slug"))
        if market_slug and len(request["market_slug_samples"]) < 5:
            request["market_slug_samples"].append(market_slug)

    requests = list(grouped.values())
    for request in requests:
        request["cities"] = sorted(request["cities"])
        request["counts_for_live_gate"] = False if request.get("supported") is False else True
        request["next_action"] = (
            "Fetch this supported external official source, then persist the station/date observation before settlement calibration."
            if request.get("supported")
            else "Add or populate this official source adapter/store before using these markets for settlement calibration."
        )
    requests.sort(
        key=lambda row: (
            not bool(row.get("supported")),
            str(row.get("settlement_source") or ""),
            str(row.get("station_code") or ""),
            str(row.get("target_date") or ""),
        )
    )
    by_station_source_date = [
        {
            "station_code": row.get("station_code"),
            "settlement_source": row.get("settlement_source"),
            "target_date": row.get("target_date"),
            "market_count": row.get("market_count"),
            "supported": row.get("supported"),
            "gap_reason": row.get("gap_reason"),
            "group_state": row.get("group_state"),
            "counts_for_live_gate": False if row.get("supported") is False else True,
            "calibration_excluded_reason": row.get("calibration_excluded_reason"),
        }
        for row in requests
    ]
    by_source: Dict[str, int] = {}
    by_station: Dict[str, int] = {}
    by_date: Dict[str, int] = {}
    for row in requests:
        _add_count(by_source, row.get("settlement_source"))
        _add_count(by_station, row.get("station_code"))
        _add_count(by_date, row.get("target_date"))
    supported_request_count = len([row for row in requests if row.get("supported")])
    unsupported_request_count = len(requests) - supported_request_count
    reuse_count = (
        int(expected_reuse_market_count)
        if expected_reuse_market_count is not None
        else sum(int(row.get("market_count") or 0) for row in requests)
    )
    return {
        "schema_version": "polyweather_official_observation_request_plan.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "input_record_count": input_count,
        "request_count": len(requests),
        "returned_request_count": min(len(requests), max(0, int(max_requests))),
        "truncated": len(requests) > max(0, int(max_requests)),
        "supported_request_count": supported_request_count,
        "unsupported_request_count": unsupported_request_count,
        "expected_reuse_market_count": reuse_count,
        "skipped_record_count": skipped_record_count,
        "by_station_source_date": by_station_source_date,
        "by_settlement_source": _count_rows(by_source, "settlement_source"),
        "by_station": _count_rows(by_station, "station_code"),
        "by_target_date": _count_rows(by_date, "target_date"),
        "requests": requests[: max(0, int(max_requests))],
    }


def build_official_value_supplement(
    record: Dict[str, Any],
    *,
    repository: Optional[Any] = None,
    collector: Optional[Any] = None,
    fetch_external: bool = False,
    allow_wunderground_proxy: bool = False,
    local_value_cache: Optional[Dict[tuple[str, str, str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not isinstance(record.get("parsed_temperature_spec"), dict) and not _record_settlement_spec(record):
        return _gap_supplement(record, "unsupported_non_temperature_market")
    if record.get("official_final_value") is not None:
        return _ready_supplement(
            record,
            source={
                "official_final_value": record.get("official_final_value"),
                "source": record.get("official_final_value_source") or "existing_record",
                "source_code": record.get("official_final_value_source_code"),
                "station_code": record.get("official_final_value_station_code") or _record_station_code(record),
                "max_observed_at": record.get("official_final_value_observed_at"),
                "observation_count": record.get("official_final_value_observation_count"),
            },
            method="existing_record",
        )

    local_cache_key = (
        _record_city(record),
        _record_target_date(record),
        _record_station_code(record),
        _record_source(record),
    )
    if local_value_cache is not None and local_cache_key in local_value_cache:
        local = dict(local_value_cache[local_cache_key])
    else:
        local = load_official_temperature_value(
            city=_record_city(record),
            target_date=_record_target_date(record),
            station_code=_record_station_code(record),
            settlement_source=_record_source(record),
            repository=repository,
        )
        if local_value_cache is not None:
            local_value_cache[local_cache_key] = dict(local)
    if local.get("status") == "ready" and local.get("official_final_value") is not None:
        return _ready_supplement(record, source=local, method="official_observation_store")
    if fetch_external:
        if _record_source(record) == "metar":
            external = _fetch_metar_recent_value(record, collector=collector)
            if external.get("status") == "ready":
                return external
            detail = dict(external.get("gap_detail") or {})
            detail["local_store_status"] = local.get("status")
            return _gap_supplement(record, str(external.get("gap_reason") or "metar_recent_gap"), detail=detail)
        if _record_source(record) == "noaa":
            external = _fetch_noaa_station_recent_value(record, collector=collector)
            if external.get("status") == "ready":
                return external
            detail = dict(external.get("gap_detail") or {})
            detail["local_store_status"] = local.get("status")
            return _gap_supplement(record, str(external.get("gap_reason") or "noaa_station_observation_recent_gap"), detail=detail)
        if _external_method_for_source(_record_source(record)) is None:
            return _gap_supplement(
                record,
                "unsupported_official_source_adapter",
                detail={
                    "settlement_source": _record_source(record) or None,
                    "local_store_status": local.get("status"),
                    "group_state": "official_source_unsupported",
                    "calibration_excluded_reason": "unsupported_official_source_adapter",
                },
            )
        external = _fetch_wunderground_value(
            record,
            collector=collector,
            allow_wunderground_proxy=allow_wunderground_proxy,
        )
        if external.get("status") == "ready":
            return external
        detail = dict(external.get("gap_detail") or {})
        detail["local_store_status"] = local.get("status")
        return _gap_supplement(record, str(external.get("gap_reason") or "external_fetch_gap"), detail=detail)
    return _gap_supplement(
        record,
        str(local.get("status") or "missing_official_observation"),
        detail={
            key: local.get(key)
            for key in ("city", "target_date", "station_code", "source_code", "attempted_sources")
            if local.get(key) is not None
        },
    )


def build_official_value_backfill_report(
    records: Iterable[Dict[str, Any]],
    *,
    repository: Optional[Any] = None,
    collector: Optional[Any] = None,
    fetch_external: bool = False,
    allow_wunderground_proxy: bool = False,
    max_gap_samples: int = 10,
    max_backfill_plan_requests: int = 20,
    max_records: Optional[int] = None,
) -> Dict[str, Any]:
    supplements: List[Dict[str, Any]] = []
    seen = 0
    shared_collector = collector
    local_value_cache: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    if fetch_external and shared_collector is None:
        shared_collector = _default_weather_collector()
    for record in records:
        if not isinstance(record, dict):
            continue
        seen += 1
        if max_records is not None and len(supplements) >= max(0, int(max_records)):
            break
        supplements.append(
            build_official_value_supplement(
                record,
                repository=repository,
                collector=shared_collector,
                fetch_external=fetch_external,
                allow_wunderground_proxy=allow_wunderground_proxy,
                local_value_cache=local_value_cache,
            )
        )

    ready = [row for row in supplements if row.get("status") == "ready" and row.get("official_final_value") is not None]
    gaps = [row for row in supplements if row.get("status") != "ready"]
    by_reason: Dict[str, int] = {}
    for row in gaps:
        reason = _text(row.get("gap_reason")) or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    hard_conclusion = "official_value_backfill_ready" if supplements and not gaps else "official_value_backfill_has_gaps"
    if not supplements:
        hard_conclusion = "official_value_backfill_no_records"
    return {
        "schema_version": OFFICIAL_VALUE_BACKFILL_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "diagnostic_only": True,
        "hard_conclusion": hard_conclusion,
        "input_record_count": seen,
        "supplement_count": len(supplements),
        "ready_count": len(ready),
        "gap_count": len(gaps),
        "fetch_external": bool(fetch_external),
        "allow_wunderground_proxy": bool(allow_wunderground_proxy),
        "gaps_by_reason": [
            {"reason": reason, "count": count}
            for reason, count in sorted(by_reason.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "official_observation_backfill_plan": _official_observation_backfill_plan(
            supplements,
            max_requests=max_backfill_plan_requests,
        ),
        "gap_samples": gaps[: max(0, int(max_gap_samples))],
        "supplements": supplements,
    }


def load_official_value_supplements(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        rows = payload.get("supplements")
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
        return [dict(payload)]
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            out.append(row)
    return out


def apply_official_value_supplements(
    records: Iterable[Dict[str, Any]],
    *,
    supplements: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for supplement in supplements:
        if not isinstance(supplement, dict) or supplement.get("status") != "ready":
            continue
        if supplement.get("official_final_value") is None:
            continue
        for key in _market_keys(supplement):
            index[key] = supplement

    out: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        if row.get("official_final_value") is None:
            supplement = None
            for key in _market_keys(row):
                supplement = index.get(key)
                if supplement is not None:
                    break
            if supplement:
                row["official_final_value"] = supplement.get("official_final_value")
                row["official_final_value_source"] = supplement.get("official_final_value_source")
                row["official_final_value_source_code"] = supplement.get("official_final_value_source_code")
                row["official_final_value_station_code"] = supplement.get("official_final_value_station_code")
                row["official_final_value_observed_at"] = supplement.get("official_final_value_observed_at")
                row["official_final_value_observation_count"] = supplement.get("official_final_value_observation_count")
                row["official_value_supplement_applied"] = True
        out.append(row)
    return out
