from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = "polyweather_weather_bucket_family_catalog.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spec(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}


def _bucket(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}


def _field(row: Dict[str, Any], field: str) -> str:
    spec = _spec(row)
    bucket = _bucket(row)
    for source in (row, spec, bucket):
        text = _text(source.get(field))
        if text:
            return text
    if field == "station_code":
        return _text(row.get("settlement_station_code")).upper()
    return ""


def _float_field(row: Dict[str, Any], field: str) -> Optional[float]:
    spec = _spec(row)
    bucket = _bucket(row)
    for source in (row, spec, bucket):
        value = _safe_float(source.get(field))
        if value is not None:
            return value
    return None


def _book(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("order_book") if isinstance(row.get("order_book"), dict) else {}


def _book_float(row: Dict[str, Any], field: str) -> Optional[float]:
    value = _safe_float(row.get(field))
    if value is not None:
        return value
    return _safe_float(_book(row).get(field))


def _side(row: Dict[str, Any]) -> str:
    return _text(row.get("side") or row.get("outcome")).lower()


def _family_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        _field(row, "event_slug"),
        _field(row, "station_code").upper(),
        _field(row, "target_date"),
        _field(row, "settlement_source").lower(),
        _field(row, "timezone"),
    )


def _bucket_sort_key(bucket: Dict[str, Any]) -> Tuple[int, float]:
    bucket_type = _text(bucket.get("bucket_type")).lower()
    threshold = float(bucket.get("threshold") or 0.0)
    if bucket_type == "le":
        return (0, threshold)
    if bucket_type == "eq":
        return (1, threshold)
    if bucket_type == "range":
        return (2, threshold)
    if bucket_type == "ge":
        return (3, threshold)
    return (9, threshold)


def _partition_diagnostics(buckets: List[Dict[str, Any]]) -> Dict[str, Any]:
    reasons: List[str] = []
    lower = [row for row in buckets if row.get("bucket_type") == "le"]
    upper = [row for row in buckets if row.get("bucket_type") == "ge"]
    exact = sorted([row for row in buckets if row.get("bucket_type") == "eq"], key=lambda row: float(row["threshold"]))
    unsupported = [row for row in buckets if row.get("bucket_type") not in {"le", "ge", "eq"}]
    if len(lower) != 1:
        reasons.append("missing_or_multiple_lower_tail")
    if len(upper) != 1:
        reasons.append("missing_or_multiple_upper_tail")
    if unsupported:
        reasons.append("unsupported_bucket_type")
    if lower and upper:
        low = float(lower[0]["threshold"])
        high = float(upper[0]["threshold"])
        if high <= low:
            reasons.append("upper_tail_not_above_lower_tail")
        exact_thresholds = [float(row["threshold"]) for row in exact]
        if exact_thresholds:
            expected = [float(value) for value in range(int(low) + 1, int(high))]
            if exact_thresholds != expected:
                reasons.append("non_continuous_exact_thresholds")
        elif high > low + 1:
            reasons.append("missing_exact_thresholds")
    else:
        exact_thresholds = [float(row["threshold"]) for row in exact]
    return {
        "has_lower_tail": len(lower) == 1,
        "has_upper_tail": len(upper) == 1,
        "exact_bucket_count": len(exact),
        "expected_exact_thresholds": (
            [float(value) for value in range(int(float(lower[0]["threshold"])) + 1, int(float(upper[0]["threshold"])))]
            if lower and upper and float(upper[0]["threshold"]) > float(lower[0]["threshold"])
            else []
        ),
        "actual_exact_thresholds": exact_thresholds,
        "partition_gap_reasons": sorted(set(reasons)),
        "is_partition_candidate": not reasons,
    }


def _empty_bucket_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    spec = _spec(row)
    return {
        "market_slug": _field(row, "market_slug"),
        "market_id": _field(row, "market_id"),
        "event_slug": _field(row, "event_slug"),
        "question": row.get("question"),
        "city": _field(row, "city"),
        "target_date": _field(row, "target_date"),
        "station_code": _field(row, "station_code").upper(),
        "settlement_source": _field(row, "settlement_source").lower(),
        "timezone": _field(row, "timezone"),
        "bucket_type": _field(row, "bucket_type").lower(),
        "threshold": _float_field(row, "threshold"),
        "upper_threshold": _float_field(row, "upper_threshold"),
        "unit": _field(row, "unit") or "C",
        "bucket_label": row.get("bucket_label") or (_bucket(row).get("label") if _bucket(row) else None),
        "market_close_time": spec.get("market_close_time") or row.get("market_close_time") or row.get("end_date"),
        "observation_window_end_time": spec.get("observation_window_end_time") or row.get("observation_window_end_time"),
        "settlement_due_time": spec.get("settlement_due_time") or row.get("settlement_due_time"),
        "yes_token_id": None,
        "no_token_id": None,
        "yes_best_ask": None,
        "no_best_ask": None,
        "yes_best_bid": None,
        "no_best_bid": None,
        "yes_ask_depth": None,
        "no_ask_depth": None,
        "yes_spread": None,
        "no_spread": None,
        "yes_orderbook_snapshot_id": None,
        "no_orderbook_snapshot_id": None,
    }


def build_weather_bucket_family_catalog(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    input_rows = [row for row in rows if isinstance(row, dict)]
    grouped: Dict[Tuple[str, str, str, str, str], Dict[str, Dict[str, Any]]] = defaultdict(dict)
    skipped_reasons: Counter[str] = Counter()
    for row in input_rows:
        if _field(row, "market_family") and _field(row, "market_family") != "temperature":
            skipped_reasons["not_temperature"] += 1
            continue
        if _field(row, "settlement_spec_status") == "unsupported" or _spec(row).get("status") == "unsupported":
            skipped_reasons["unsupported_settlement_spec"] += 1
            continue
        key = _family_key(row)
        if not all(key[:4]):
            skipped_reasons["missing_family_key"] += 1
            continue
        market_slug = _field(row, "market_slug")
        bucket_type = _field(row, "bucket_type").lower()
        threshold = _float_field(row, "threshold")
        if not market_slug or bucket_type not in {"le", "ge", "eq", "range"} or threshold is None:
            skipped_reasons["missing_bucket_fields"] += 1
            continue
        bucket = grouped[key].setdefault(market_slug, _empty_bucket_entry(row))
        side = _side(row)
        if side == "yes":
            bucket["yes_token_id"] = _text(row.get("token_id")) or None
            bucket["yes_best_ask"] = _book_float(row, "best_ask")
            bucket["yes_best_bid"] = _book_float(row, "best_bid")
            bucket["yes_ask_depth"] = _book_float(row, "ask_depth_usdc_3c")
            bucket["yes_spread"] = _book_float(row, "spread")
            bucket["yes_orderbook_snapshot_id"] = row.get("snapshot_id") or row.get("orderbook_snapshot_id")
        elif side == "no":
            bucket["no_token_id"] = _text(row.get("token_id")) or None
            bucket["no_best_ask"] = _book_float(row, "best_ask")
            bucket["no_best_bid"] = _book_float(row, "best_bid")
            bucket["no_ask_depth"] = _book_float(row, "ask_depth_usdc_3c")
            bucket["no_spread"] = _book_float(row, "spread")
            bucket["no_orderbook_snapshot_id"] = row.get("snapshot_id") or row.get("orderbook_snapshot_id")

    families: List[Dict[str, Any]] = []
    gap_rows: List[Dict[str, Any]] = []
    gap_counter: Counter[str] = Counter()
    for key, by_market in sorted(grouped.items()):
        buckets = sorted(by_market.values(), key=_bucket_sort_key)
        diagnostics = _partition_diagnostics(buckets)
        event_slug, station_code, target_date, settlement_source, timezone_name = key
        first = buckets[0] if buckets else {}
        family = {
            "schema_version": "polyweather_weather_bucket_family.v1",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "event_slug": event_slug,
            "target_date": target_date,
            "city": first.get("city"),
            "station_code": station_code,
            "settlement_source": settlement_source,
            "timezone": timezone_name,
            "bucket_count": len(buckets),
            "buckets": buckets,
            "bucket_types": [row.get("bucket_type") for row in buckets],
            "has_lower_tail": diagnostics["has_lower_tail"],
            "has_upper_tail": diagnostics["has_upper_tail"],
            "exact_bucket_count": diagnostics["exact_bucket_count"],
            "is_partition_candidate": diagnostics["is_partition_candidate"],
            "partition_gap_reasons": diagnostics["partition_gap_reasons"],
            "expected_exact_thresholds": diagnostics["expected_exact_thresholds"],
            "actual_exact_thresholds": diagnostics["actual_exact_thresholds"],
            "market_slugs": [row.get("market_slug") for row in buckets],
            "yes_token_ids": [row.get("yes_token_id") for row in buckets if row.get("yes_token_id")],
            "no_token_ids": [row.get("no_token_id") for row in buckets if row.get("no_token_id")],
        }
        families.append(family)
        if family["partition_gap_reasons"]:
            for reason in family["partition_gap_reasons"]:
                gap_counter[reason] += 1
            gap_rows.append(
                {
                    "event_slug": event_slug,
                    "station_code": station_code,
                    "target_date": target_date,
                    "settlement_source": settlement_source,
                    "bucket_count": len(buckets),
                    "partition_gap_reasons": family["partition_gap_reasons"],
                    "market_slugs": family["market_slugs"][:10],
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "input_row_count": len(input_rows),
        "family_count": len(families),
        "partition_candidate_count": len([row for row in families if row.get("is_partition_candidate")]),
        "gap_counts": [{"reason": key, "count": count} for key, count in sorted(gap_counter.items())],
        "skipped_row_counts": [{"reason": key, "count": count} for key, count in sorted(skipped_reasons.items())],
        "families": families,
        "gaps": gap_rows,
    }


__all__ = ["SCHEMA_VERSION", "build_weather_bucket_family_catalog"]
