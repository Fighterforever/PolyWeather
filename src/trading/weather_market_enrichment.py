from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.data_collection.city_registry import ALIASES, CITY_REGISTRY


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
MONTH_RE = "|".join(MONTHS)
DATE_RE = re.compile(
    rf"\bon\s+({MONTH_RE})\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
TEMP_CONDITION_RE = re.compile(
    r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:°\s*)?([cf])?\s*"
    r"(or\s+above|or\s+higher|or\s+more|or\s+below|or\s+lower|or\s+less|above|below)\b",
    re.IGNORECASE,
)
TEMP_EXACT_RE = re.compile(
    r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:°\s*)?([cf])?\b",
    re.IGNORECASE,
)
TEMP_RANGE_RE = re.compile(
    r"\bbetween\s+(-?\d+(?:\.\d+)?)\s*(?:-|to|and)\s*"
    r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?([cf])?\b",
    re.IGNORECASE,
)
SLUG_TEMP_CONDITION_RE = re.compile(
    r"(?:^|-)(-?\d+(?:\.\d+)?)(?:c|f)?(orabove|orhigher|ormore|orbelow|orlower|orless)(?:$|-)",
    re.IGNORECASE,
)
SLUG_TEMP_EXACT_RE = re.compile(
    r"(?:^|-)(-?\d+(?:\.\d+)?)(c|f)(?:$|-)",
    re.IGNORECASE,
)
SLUG_TEMP_RANGE_RE = re.compile(
    r"(?:^|-)between-(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)(c|f)(?:$|-)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TemperatureOutcomeSpec:
    city: str
    target_date: Optional[str]
    threshold: float
    comparator: str
    unit: str
    upper_threshold: Optional[float] = None
    market_type: str = "maxtemp"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9°.+-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _row_text(row: Dict[str, Any]) -> str:
    return " ".join(
        str(row.get(field) or "")
        for field in (
            "question",
            "market_slug",
            "event_title",
            "event_slug",
            "description",
        )
    )


def _parse_end_date_year(row: Dict[str, Any]) -> Optional[int]:
    raw = str(row.get("end_date") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).year
    except Exception:
        match = re.search(r"\b(20\d{2})\b", raw)
        return int(match.group(1)) if match else None


def parse_market_date(row: Dict[str, Any]) -> Optional[str]:
    text = _row_text(row)
    match = DATE_RE.search(text)
    if not match:
        return None
    month = MONTHS[match.group(1).lower()]
    day = int(match.group(2))
    year = int(match.group(3)) if match.group(3) else _parse_end_date_year(row)
    if year is None:
        return None
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def match_city_from_market_text(row: Dict[str, Any]) -> Optional[str]:
    normalized = _normalize_text(_row_text(row))
    candidates: List[Tuple[int, str, str]] = []
    for city_key, meta in CITY_REGISTRY.items():
        names = {city_key, str(meta.get("name") or "")}
        if city_key == "new york":
            names.update({"new york city", "nyc"})
        elif city_key == "los angeles":
            names.add("la")
        for name in names:
            norm_name = _normalize_text(name)
            if not norm_name:
                continue
            candidates.append((len(norm_name), norm_name, city_key))
    for alias, city_key in ALIASES.items():
        if city_key not in CITY_REGISTRY:
            continue
        norm_alias = _normalize_text(alias)
        if norm_alias:
            candidates.append((len(norm_alias), norm_alias, city_key))
    for _, norm_name, city_key in sorted(candidates, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(norm_name)}(?![a-z0-9])", normalized):
            return city_key
    return None


def _normalize_comparator(text: str) -> str:
    lowered = str(text or "").lower().replace(" ", "")
    if lowered in {"orabove", "orhigher", "ormore", "above"}:
        return "ge"
    if lowered in {"orbelow", "orlower", "orless", "below"}:
        return "le"
    if lowered in {"exact", "equals", "equal"}:
        return "eq"
    return ""


def parse_temperature_outcome_spec(row: Dict[str, Any]) -> Optional[TemperatureOutcomeSpec]:
    text = _row_text(row)
    normalized = _normalize_text(text)
    if "highest temperature" not in normalized:
        return None
    city = match_city_from_market_text(row)
    if not city:
        return None

    condition_match = TEMP_CONDITION_RE.search(text)
    threshold: Optional[float] = None
    upper_threshold: Optional[float] = None
    unit = "C"
    comparator = ""
    range_match = TEMP_RANGE_RE.search(text)
    slug_range_match = SLUG_TEMP_RANGE_RE.search(str(row.get("market_slug") or ""))
    if range_match:
        threshold = _safe_float(range_match.group(1))
        upper_threshold = _safe_float(range_match.group(2))
        if range_match.group(3):
            unit = range_match.group(3).upper()
        comparator = "range"
    elif slug_range_match:
        threshold = _safe_float(slug_range_match.group(1))
        upper_threshold = _safe_float(slug_range_match.group(2))
        unit = slug_range_match.group(3).upper()
        comparator = "range"
    elif condition_match:
        threshold = _safe_float(condition_match.group(1))
        if condition_match.group(2):
            unit = condition_match.group(2).upper()
        comparator = _normalize_comparator(condition_match.group(3))
    else:
        slug_match = SLUG_TEMP_CONDITION_RE.search(str(row.get("market_slug") or ""))
        if slug_match:
            threshold = _safe_float(slug_match.group(1))
            comparator = _normalize_comparator(slug_match.group(2))
        else:
            exact_match = TEMP_EXACT_RE.search(text)
            slug_exact_match = SLUG_TEMP_EXACT_RE.search(str(row.get("market_slug") or ""))
            if exact_match:
                threshold = _safe_float(exact_match.group(1))
                if exact_match.group(2):
                    unit = exact_match.group(2).upper()
                comparator = "eq"
            elif slug_exact_match:
                threshold = _safe_float(slug_exact_match.group(1))
                unit = slug_exact_match.group(2).upper()
                comparator = "eq"

    if threshold is None or comparator not in {"ge", "le", "eq", "range"}:
        return None
    if comparator == "range":
        if upper_threshold is None:
            return None
        threshold, upper_threshold = sorted((threshold, upper_threshold))
    city_meta = CITY_REGISTRY.get(city) or {}
    if city_meta.get("use_fahrenheit"):
        unit = "F"
    return TemperatureOutcomeSpec(
        city=city,
        target_date=parse_market_date(row),
        threshold=threshold,
        comparator=comparator,
        unit=unit,
        upper_threshold=upper_threshold,
    )


def _distribution_items(model_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    for field in ("distribution_full", "distribution_preview", "probabilities"):
        value = model_row.get(field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            distribution = value.get("distribution_all") or value.get("distribution")
            if isinstance(distribution, list):
                return [item for item in distribution if isinstance(item, dict)]
    return []


def probability_for_temperature_spec(
    distribution: Iterable[Dict[str, Any]],
    spec: TemperatureOutcomeSpec,
    *,
    outcome: str,
) -> Optional[float]:
    total = 0.0
    matched = 0.0
    for item in distribution:
        value = _safe_float(item.get("value"))
        probability = _safe_float(item.get("probability"))
        if value is None or probability is None or probability <= 0:
            continue
        total += probability
        if spec.comparator == "ge" and value >= spec.threshold:
            matched += probability
        elif spec.comparator == "le" and value <= spec.threshold:
            matched += probability
        elif spec.comparator == "eq" and abs(value - spec.threshold) < 1e-9:
            matched += probability
        elif (
            spec.comparator == "range"
            and spec.upper_threshold is not None
            and spec.threshold <= value <= spec.upper_threshold
        ):
            matched += probability
    if total <= 0:
        return None
    yes_probability = max(0.0, min(1.0, matched / total))
    side = str(outcome or "").strip().lower()
    probability = yes_probability if side == "yes" else 1.0 - yes_probability
    return round(probability, 6)


def _scan_model_index(scan_payload: Dict[str, Any]) -> Dict[Tuple[str, Optional[str]], Dict[str, Any]]:
    rows = scan_payload.get("rows") if isinstance(scan_payload, dict) else []
    index: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city") or "").strip().lower()
        if not city:
            continue
        selected_date = str(row.get("selected_date") or row.get("local_date") or "").strip() or None
        index[(city, selected_date)] = row
        index.setdefault((city, None), row)
    return index


def temperature_model_targets_from_payload(
    polymarket_payload: Dict[str, Any],
) -> Dict[str, List[Optional[str]]]:
    rows = polymarket_payload.get("rows") if isinstance(polymarket_payload, dict) else []
    targets: Dict[str, List[Optional[str]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        spec = parse_temperature_outcome_spec(row)
        if spec is None:
            continue
        city_targets = targets.setdefault(spec.city, [])
        if spec.target_date not in city_targets:
            city_targets.append(spec.target_date)
    return targets


def enrich_polymarket_payload_with_scan_models(
    polymarket_payload: Dict[str, Any],
    scan_payload: Dict[str, Any],
) -> Dict[str, Any]:
    rows = polymarket_payload.get("rows") if isinstance(polymarket_payload, dict) else []
    model_index = _scan_model_index(scan_payload)
    enriched_rows: List[Dict[str, Any]] = []
    joined = 0
    unsupported = 0
    missing_model = 0
    missing_distribution = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        enriched = dict(row)
        spec = parse_temperature_outcome_spec(enriched)
        if spec is None:
            enriched["model_join_status"] = "unsupported_market_type"
            unsupported += 1
            enriched_rows.append(enriched)
            continue

        model_row = model_index.get((spec.city, spec.target_date)) or model_index.get((spec.city, None))
        enriched["city"] = spec.city
        enriched["selected_date"] = spec.target_date
        if spec.comparator == "range" and spec.upper_threshold is not None:
            enriched["bucket_label"] = f"{spec.threshold:g}-{spec.upper_threshold:g}°{spec.unit}"
        else:
            comparator_label = {"ge": ">=", "le": "<=", "eq": "="}[spec.comparator]
            enriched["bucket_label"] = f"{comparator_label} {spec.threshold:g}°{spec.unit}"
        enriched["parsed_temperature_spec"] = asdict(spec)
        if not model_row:
            enriched["model_join_status"] = "missing_scan_model_row"
            missing_model += 1
            enriched_rows.append(enriched)
            continue

        distribution = _distribution_items(model_row)
        if not distribution:
            enriched["model_join_status"] = "missing_probability_distribution"
            missing_distribution += 1
            enriched_rows.append(enriched)
            continue

        model_probability = probability_for_temperature_spec(
            distribution,
            spec,
            outcome=str(enriched.get("outcome") or enriched.get("side") or ""),
        )
        market_price = _safe_float(enriched.get("price"))
        if model_probability is None or market_price is None:
            enriched["model_join_status"] = "missing_probability_or_price"
            missing_distribution += 1
            enriched_rows.append(enriched)
            continue

        edge_percent = (model_probability - market_price) * 100.0
        enriched.update(
            {
                "model_join_status": "joined",
                "model_probability": round(model_probability, 6),
                "edge_percent": round(edge_percent, 4),
                "final_score": round(edge_percent * 10.0, 4),
                "source_model_row_id": model_row.get("id") or model_row.get("row_id"),
                "deb_prediction": model_row.get("deb_prediction"),
                "distribution_preview": distribution[:8],
            }
        )
        joined += 1
        enriched_rows.append(enriched)

    diagnostics = dict(polymarket_payload.get("diagnostics") or {})
    diagnostics["model_join"] = {
        "scan_snapshot_id": scan_payload.get("snapshot_id") if isinstance(scan_payload, dict) else None,
        "scan_status": scan_payload.get("status") if isinstance(scan_payload, dict) else None,
        "scan_error": (
            scan_payload.get("error_message")
            or scan_payload.get("stale_reason")
            or scan_payload.get("last_error")
        )
        if isinstance(scan_payload, dict)
        else None,
        "scan_rows": len(scan_payload.get("rows") or []) if isinstance(scan_payload, dict) else 0,
        "rows_seen": len(rows or []),
        "joined": joined,
        "unsupported_market_type": unsupported,
        "missing_scan_model_row": missing_model,
        "missing_probability_distribution": missing_distribution,
    }
    return {
        **polymarket_payload,
        "rows": enriched_rows,
        "diagnostics": diagnostics,
    }
