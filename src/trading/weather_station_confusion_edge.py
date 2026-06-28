from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = "polyweather_station_confusion_edge.v1"
BIAS_SCHEMA_VERSION = "polyweather_station_city_bias.v1"
CANDIDATE_SCHEMA_VERSION = "polyweather_station_confusion_candidate.v1"
STRATEGY_ID = "station_confusion_edge"
SUPPORTED_SETTLEMENT_SOURCES = {"metar", "noaa", "wunderground", "aeroweb"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_dict(row: Dict[str, Any], field: str) -> Dict[str, Any]:
    value = row.get(field)
    return value if isinstance(value, dict) else {}


def _first_text(row: Dict[str, Any], *fields: str) -> str:
    sources = (row, _row_dict(row, "settlement_spec"), _row_dict(row, "market_bucket"))
    for source in sources:
        for field in fields:
            text = str(source.get(field) or "").strip()
            if text:
                return text
    return ""


def _first_float(row: Dict[str, Any], *fields: str) -> Optional[float]:
    sources = (row, _row_dict(row, "settlement_spec"), _row_dict(row, "market_bucket"))
    for source in sources:
        for field in fields:
            value = _safe_float(source.get(field))
            if value is not None:
                return value
    return None


def _high_value(row: Dict[str, Any], *fields: str) -> Optional[float]:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for field in fields:
        value = _safe_float(payload.get(field))
        if value is not None:
            return value
    return None


def _station_key(row: Dict[str, Any]) -> tuple[str, str, str, str]:
    city = str(row.get("city") or row.get("city_key") or "").strip().lower()
    station = str(row.get("station_code") or row.get("settlement_station_code") or "").strip().upper()
    target = str(row.get("target_date") or row.get("target_date_local") or row.get("date") or "").strip()
    raw_source = str(row.get("settlement_source") or row.get("source") or "metar").strip().lower()
    source = "metar" if raw_source.startswith("open_meteo") else raw_source
    return city, station, target, source


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _std(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round(math.sqrt(variance), 6)


def build_station_bias_table(
    *,
    station_rows: Iterable[Dict[str, Any]],
    city_grid_rows: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    station_by_key: Dict[tuple[str, str, str, str], float] = {}
    city_by_key: Dict[tuple[str, str, str, str], float] = {}
    for row in station_rows:
        if not isinstance(row, dict):
            continue
        key = _station_key(row)
        high = _high_value(row, "station_final_high", "station_final_high_c", "official_final_value", "max_temp_c", "current_high_c")
        if key[1] and key[2] and high is not None:
            station_by_key[key] = float(high)
    for row in city_grid_rows:
        if not isinstance(row, dict):
            continue
        key = _station_key(row)
        high = _high_value(
            row,
            "city_grid_final_high",
            "city_grid_final_high_c",
            "grid_final_high_c",
            "predicted_daily_high",
            "max_temp_c",
        )
        if key[1] and key[2] and high is not None:
            city_by_key[key] = float(high)
    paired_rows: List[Dict[str, Any]] = []
    by_station: Dict[tuple[str, str, str], List[float]] = defaultdict(list)
    for key in sorted(set(station_by_key) & set(city_by_key)):
        city, station, target_date, source = key
        station_high = station_by_key[key]
        city_high = city_by_key[key]
        bias = station_high - city_high
        by_station[(city, station, source)].append(bias)
        paired_rows.append(
            {
                "schema_version": BIAS_SCHEMA_VERSION,
                "city": city,
                "station_code": station,
                "settlement_source": source,
                "target_date": target_date,
                "station_final_high": station_high,
                "city_grid_final_high": city_high,
                "station_minus_city_bias": round(bias, 6),
                "absolute_bias": round(abs(bias), 6),
                "bias_direction": "station_hotter" if bias > 0 else ("station_colder" if bias < 0 else "flat"),
            }
        )
    station_biases: List[Dict[str, Any]] = []
    for (city, station, source), values in sorted(by_station.items()):
        station_biases.append(
            {
                "city": city,
                "station_code": station,
                "settlement_source": source,
                "historical_bias_mean": _mean(values),
                "historical_bias_std": _std(values),
                "sample_count": len(values),
                "absolute_bias_mean": _mean([abs(value) for value in values]),
                "bias_direction": "station_hotter" if (sum(values) / len(values)) > 0 else ("station_colder" if (sum(values) / len(values)) < 0 else "flat"),
            }
        )
    return {
        "schema_version": "polyweather_station_bias_table.v1",
        "strategy_id": STRATEGY_ID,
        "platform": "polymarket",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "station_bias_sample_count": len(paired_rows),
        "paired_rows": paired_rows,
        "station_biases": sorted(
            station_biases,
            key=lambda row: (abs(float(row.get("historical_bias_mean") or 0.0)), int(row.get("sample_count") or 0)),
            reverse=True,
        ),
    }


def _probability_for_threshold(value: Optional[float], *, bucket_type: str, threshold: Optional[float]) -> Optional[float]:
    if value is None or threshold is None:
        return None
    if bucket_type == "ge":
        return 1.0 if value >= threshold else 0.0
    if bucket_type == "le":
        return 1.0 if value <= threshold else 0.0
    return None


def _market_probability(row: Dict[str, Any]) -> Optional[float]:
    for field in ("market_implied_de_vig_yes_probability", "market_implied_yes_price", "market_probability", "best_ask", "ask", "price"):
        value = _safe_float(row.get(field))
        if value is not None:
            return max(0.0, min(1.0, value))
    return None


def _price_bucket(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "missing"
    if price < 0.005:
        return "price_lt_0_005"
    if price < 0.03:
        return "price_0_005_to_0_03"
    return "price_ge_0_03"


def _bias_lookup(bias_table: Dict[str, Any]) -> Dict[tuple[str, str, str], Dict[str, Any]]:
    lookup: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for row in bias_table.get("station_biases") or []:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city") or "").strip().lower()
        station = str(row.get("station_code") or "").strip().upper()
        source = str(row.get("settlement_source") or "metar").strip().lower()
        lookup[(city, station, source)] = row
    return lookup


def scan_station_confusion_candidates(
    rows: Iterable[Dict[str, Any]],
    *,
    bias_table: Dict[str, Any],
    generated_at: Optional[str] = None,
    min_abs_confusion_edge: float = 0.35,
    min_bias_sample_count: int = 3,
    min_ask_depth: float = 1.0,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    generated_at = generated_at or utc_now_iso()
    lookup = _bias_lookup(bias_table)
    candidates: List[Dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        market_family = _first_text(row, "market_family").lower()
        bucket_type = _first_text(row, "bucket_type").lower()
        side = _first_text(row, "side").lower()
        city = _first_text(row, "city").lower()
        station = _first_text(row, "station_code").upper()
        source = _first_text(row, "settlement_source").lower()
        threshold = _first_float(row, "threshold")
        ask = _safe_float(row.get("best_ask") or row.get("ask") or row.get("price"))
        depth = _safe_float(row.get("ask_depth") or row.get("ask_depth_usdc_3c") or row.get("execution_liquidity"))
        if depth is None and isinstance(row.get("order_book"), dict):
            depth = _safe_float(row["order_book"].get("ask_depth_usdc_3c"))
        market_prob = _market_probability(row)
        bias = lookup.get((city, station, source)) or {}
        sample_count = _safe_int(bias.get("sample_count"))
        station_high = _high_value(row, "station_forecast_high_c", "station_expected_high_c", "station_current_high_c", "current_high_c")
        city_high = _high_value(row, "city_grid_forecast_high_c", "city_expected_high_c", "city_grid_current_high_c")
        if station_high is None and city_high is not None and bias.get("historical_bias_mean") is not None:
            station_high = city_high + float(bias["historical_bias_mean"])
        if city_high is None and station_high is not None and bias.get("historical_bias_mean") is not None:
            city_high = station_high - float(bias["historical_bias_mean"])
        station_prob = _probability_for_threshold(station_high, bucket_type=bucket_type, threshold=threshold)
        city_prob = _probability_for_threshold(city_high, bucket_type=bucket_type, threshold=threshold)
        rejection: Optional[str] = None
        if market_family and market_family != "temperature":
            rejection = "not_temperature"
        elif bucket_type not in {"ge", "le"}:
            rejection = "bucket_not_ge_le"
        elif side != "yes":
            rejection = "side_not_yes_shadow"
        elif source not in SUPPORTED_SETTLEMENT_SOURCES:
            rejection = "unsupported_settlement_source"
        elif _price_bucket(ask) == "price_lt_0_005":
            rejection = "dust_price"
        elif ask is None:
            rejection = "missing_ask"
        elif depth is None or depth < float(min_ask_depth):
            rejection = "ask_depth_below_min"
        elif sample_count < int(min_bias_sample_count):
            rejection = "bias_sample_too_small"
        elif station_prob is None or city_prob is None:
            rejection = "missing_station_or_city_probability"
        elif market_prob is None:
            rejection = "missing_market_probability"
        if rejection is not None:
            reasons[rejection] += 1
            continue
        assert station_prob is not None and city_prob is not None and market_prob is not None
        confusion_edge = station_prob - city_prob
        station_edge = station_prob - market_prob
        if abs(confusion_edge) < float(min_abs_confusion_edge):
            reasons["confusion_edge_below_threshold"] += 1
            continue
        decision = "paper_buy_yes_shadow" if station_edge > 0 else "paper_no_trade_station_edge_nonpositive"
        if station_edge <= 0:
            reasons["station_edge_nonpositive"] += 1
            continue
        candidates.append(
            {
                "schema_version": CANDIDATE_SCHEMA_VERSION,
                "strategy_id": STRATEGY_ID,
                "platform": "polymarket",
                "generated_at": generated_at,
                "market_slug": _first_text(row, "market_slug"),
                "event_slug": _first_text(row, "event_slug"),
                "token_id": _first_text(row, "token_id"),
                "side": side,
                "city": city,
                "station_code": station,
                "settlement_source": source,
                "target_date": _first_text(row, "target_date", "selected_date"),
                "bucket_type": bucket_type,
                "threshold": threshold,
                "station_final_high": station_high,
                "city_grid_final_high": city_high,
                "station_minus_city_bias": bias.get("historical_bias_mean"),
                "absolute_bias": abs(float(bias.get("historical_bias_mean") or 0.0)),
                "bias_direction": bias.get("bias_direction"),
                "historical_bias_mean": bias.get("historical_bias_mean"),
                "historical_bias_std": bias.get("historical_bias_std"),
                "sample_count": sample_count,
                "station_probability": station_prob,
                "city_probability": city_prob,
                "market_implied_probability": market_prob,
                "station_edge": round(station_edge, 6),
                "confusion_edge": round(confusion_edge, 6),
                "ask": ask,
                "ask_depth": depth,
                "decision": decision,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return sorted(candidates, key=lambda row: (float(row.get("station_edge") or 0.0), abs(float(row.get("confusion_edge") or 0.0))), reverse=True), dict(sorted(reasons.items()))


def build_station_confusion_edge_report(
    rows: Iterable[Dict[str, Any]],
    *,
    station_rows: Iterable[Dict[str, Any]],
    city_grid_rows: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
    min_abs_confusion_edge: float = 0.35,
    min_bias_sample_count: int = 3,
    min_ask_depth: float = 1.0,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    bias_table = build_station_bias_table(
        station_rows=station_rows,
        city_grid_rows=city_grid_rows,
        generated_at=generated_at,
    )
    candidates, reason_counts = scan_station_confusion_candidates(
        rows,
        bias_table=bias_table,
        generated_at=generated_at,
        min_abs_confusion_edge=min_abs_confusion_edge,
        min_bias_sample_count=min_bias_sample_count,
        min_ask_depth=min_ask_depth,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "platform": "polymarket",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "station_bias_sample_count": int(bias_table.get("station_bias_sample_count") or 0),
        "active_candidate_count": len(candidates),
        "candidate_count": len(candidates),
        "top_station_biases": (bias_table.get("station_biases") or [])[:10],
        "candidates": candidates,
        "no_candidate_reason_counts": reason_counts,
        "station_bias_table": bias_table,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


__all__ = [
    "SCHEMA_VERSION",
    "STRATEGY_ID",
    "build_station_bias_table",
    "build_station_confusion_edge_report",
    "load_jsonl",
    "scan_station_confusion_candidates",
    "write_json",
    "write_jsonl",
]
