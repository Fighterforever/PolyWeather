from __future__ import annotations

import re
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.data_collection.city_registry import CITY_REGISTRY


SETTLEMENT_SPEC_SCHEMA_VERSION = "polyweather_weather_settlement_spec.v1"
MARKET_BUCKET_SCHEMA_VERSION = "polyweather_weather_market_bucket.v1"
RESOLVED_OUTCOME_SCHEMA_VERSION = "polyweather_weather_resolved_outcome.v1"


@dataclass(frozen=True)
class MarketBucket:
    platform: str
    market_id: Optional[str]
    market_slug: Optional[str]
    token_id: Optional[str]
    side: Optional[str]
    outcome: Optional[str]
    bucket_type: str
    threshold: float
    upper_threshold: Optional[float]
    unit: str
    label: str

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": MARKET_BUCKET_SCHEMA_VERSION, **asdict(self)}


@dataclass(frozen=True)
class SettlementSpec:
    platform: str
    market_id: Optional[str]
    market_slug: Optional[str]
    event_slug: Optional[str]
    question: Optional[str]
    market_family: str
    city: str
    city_display_name: str
    station_code: str
    station_label: str
    settlement_source: str
    settlement_url: Optional[str]
    target_date: str
    timezone: str
    metric: str
    unit: str
    bucket_type: str
    threshold: float
    upper_threshold: Optional[float]
    rounding: str
    rule_text: str
    rule_hash: str
    end_time: str
    status: str = "supported"
    unsupported_reasons: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["unsupported_reasons"] = list(self.unsupported_reasons)
        return {"schema_version": SETTLEMENT_SPEC_SCHEMA_VERSION, **payload}


@dataclass(frozen=True)
class ResolvedOutcome:
    platform: str
    market_id: Optional[str]
    market_slug: Optional[str]
    token_id: Optional[str]
    winning_token_id: Optional[str]
    outcome: Optional[str]
    official_final_value: Optional[float]
    payout: Optional[float]
    settlement_source: Optional[str]
    rule_hash: Optional[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": RESOLVED_OUTCOME_SCHEMA_VERSION, **asdict(self)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _end_time(row: Dict[str, Any]) -> Optional[str]:
    for field in ("end_date", "endDate", "end_time", "endTime"):
        raw = _text(row.get(field))
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return None


def _stable_json_hash(value: Any, *, length: int = 16) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[: max(1, int(length))]


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


def _bucket_label(bucket_type: str, threshold: float, upper_threshold: Optional[float], unit: str) -> str:
    if bucket_type == "ge":
        return f">= {threshold:g}°{unit}"
    if bucket_type == "le":
        return f"<= {threshold:g}°{unit}"
    if bucket_type == "eq":
        return f"= {threshold:g}°{unit}"
    if bucket_type == "range" and upper_threshold is not None:
        return f"{threshold:g}-{upper_threshold:g}°{unit}"
    return f"{threshold:g}°{unit}"


def _rule_text(row: Dict[str, Any], *, city_display_name: str, station_label: str) -> str:
    explicit = _text(row.get("rules") or row.get("resolution_rules") or row.get("description"))
    if explicit:
        return explicit
    question = _text(row.get("question"))
    if question:
        return f"{question} Settlement station: {station_label} ({city_display_name})."
    return f"Highest temperature market for {city_display_name}. Settlement station: {station_label}."


def _station_fields(city: str) -> Dict[str, Optional[str]]:
    meta = CITY_REGISTRY.get(city) or {}
    station_code = _text(meta.get("settlement_station_code") or meta.get("icao"))
    station_label = _text(meta.get("settlement_station_label") or meta.get("airport_name") or station_code)
    source = _text(meta.get("settlement_source") or ("metar" if station_code else ""))
    return {
        "city_display_name": _text(meta.get("name") or city),
        "station_code": station_code or None,
        "station_label": station_label or None,
        "settlement_source": source or None,
        "settlement_url": _text(meta.get("settlement_url")) or None,
        "timezone": _timezone_from_offset(meta.get("tz_offset")),
    }


def build_market_bucket(
    row: Dict[str, Any],
    *,
    bucket_type: str,
    threshold: float,
    unit: str,
    upper_threshold: Optional[float] = None,
) -> MarketBucket:
    return MarketBucket(
        platform=_text(row.get("platform")) or "polymarket",
        market_id=_text(row.get("market_id")) or None,
        market_slug=_text(row.get("market_slug") or row.get("slug")) or None,
        token_id=_text(row.get("token_id")) or None,
        side=_text(row.get("side")).lower() or None,
        outcome=_text(row.get("outcome")) or None,
        bucket_type=bucket_type,
        threshold=float(threshold),
        upper_threshold=upper_threshold,
        unit=unit.upper(),
        label=_bucket_label(bucket_type, float(threshold), upper_threshold, unit.upper()),
    )


def build_temperature_settlement_spec(
    row: Dict[str, Any],
    *,
    city: Optional[str],
    target_date: Optional[str],
    bucket_type: str,
    threshold: Optional[float],
    unit: str,
    upper_threshold: Optional[float] = None,
    market_family: str = "temperature",
) -> tuple[Optional[SettlementSpec], List[str]]:
    """Build a canonical settlement spec for a parsed temperature market row.

    The spec is intentionally conservative: a row without station/source/date/end
    metadata is diagnosable, but it is not eligible for strict trading signals.
    """

    unsupported: List[str] = []
    city_key = _text(city).lower()
    if not city_key:
        unsupported.append("missing_city")
    station = _station_fields(city_key) if city_key else {}
    station_code = _text(station.get("station_code"))
    station_label = _text(station.get("station_label"))
    source = _text(station.get("settlement_source"))
    timezone_name = _text(station.get("timezone"))
    if not station_code:
        unsupported.append("missing_station_code")
    if not station_label:
        unsupported.append("missing_station_label")
    if not source:
        unsupported.append("missing_settlement_source")
    if not timezone_name:
        unsupported.append("missing_timezone")
    if not _text(target_date):
        unsupported.append("missing_target_date")
    threshold_value = _safe_float(threshold)
    if threshold_value is None:
        unsupported.append("missing_threshold")
    end_time = _end_time(row)
    if not end_time:
        unsupported.append("missing_end_time")
    normalized_bucket_type = _text(bucket_type).lower()
    if normalized_bucket_type not in {"ge", "le", "eq", "range"}:
        unsupported.append("unsupported_bucket_type")
    if normalized_bucket_type == "range" and _safe_float(upper_threshold) is None:
        unsupported.append("missing_upper_threshold")

    if unsupported:
        return None, sorted(set(unsupported))

    display_name = _text(station.get("city_display_name")) or city_key
    unit_value = (_text(unit) or "C").upper()
    rule = _rule_text(row, city_display_name=display_name, station_label=station_label)
    rule_hash = _stable_json_hash(
        {
            "platform": _text(row.get("platform")) or "polymarket",
            "market_slug": _text(row.get("market_slug") or row.get("slug")),
            "city": city_key,
            "station_code": station_code,
            "settlement_source": source,
            "target_date": target_date,
            "metric": "daily_high_temperature",
            "bucket_type": normalized_bucket_type,
            "threshold": threshold_value,
            "upper_threshold": _safe_float(upper_threshold),
            "rule_text": re.sub(r"\s+", " ", rule).strip(),
        },
        length=20,
    )
    return (
        SettlementSpec(
            platform=_text(row.get("platform")) or "polymarket",
            market_id=_text(row.get("market_id")) or None,
            market_slug=_text(row.get("market_slug") or row.get("slug")) or None,
            event_slug=_text(row.get("event_slug")) or None,
            question=_text(row.get("question")) or None,
            market_family=market_family,
            city=city_key,
            city_display_name=display_name,
            station_code=station_code,
            station_label=station_label,
            settlement_source=source,
            settlement_url=_text(station.get("settlement_url")) or None,
            target_date=_text(target_date),
            timezone=timezone_name,
            metric="daily_high_temperature",
            unit=unit_value,
            bucket_type=normalized_bucket_type,
            threshold=float(threshold_value),
            upper_threshold=_safe_float(upper_threshold),
            rounding="integer_nearest",
            rule_text=rule,
            rule_hash=rule_hash,
            end_time=end_time,
        ),
        [],
    )


def unsupported_settlement_diagnostics(row: Dict[str, Any], reasons: List[str]) -> Dict[str, Any]:
    return {
        "schema_version": SETTLEMENT_SPEC_SCHEMA_VERSION,
        "status": "unsupported",
        "unsupported_reasons": sorted(set(str(reason) for reason in reasons if str(reason))),
        "market_id": _text(row.get("market_id")) or None,
        "market_slug": _text(row.get("market_slug") or row.get("slug")) or None,
        "question": _text(row.get("question")) or None,
    }
