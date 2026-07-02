from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = "polyweather_weather_market_implied.v1"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _bucket_type(row: Dict[str, Any]) -> str:
    bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}
    value = str(bucket.get("bucket_type") or row.get("bucket_type") or "").strip().lower()
    if value:
        return value
    label = str(row.get("bucket_label") or "").strip()
    if label.startswith(">="):
        return "ge"
    if label.startswith("<="):
        return "le"
    if label.startswith("="):
        return "eq"
    if "-" in label:
        return "range"
    return "unknown"


def _threshold(row: Dict[str, Any]) -> Optional[float]:
    bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}
    for value in (bucket.get("threshold"), row.get("threshold")):
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    return _safe_float(spec.get("threshold"))


def _event_group_key(row: Dict[str, Any]) -> str:
    return "|".join(
        str(row.get(field) or "")
        for field in ("event_id", "event_slug", "city", "selected_date")
    )


def _market_key(row: Dict[str, Any], index: int = 0) -> str:
    return str(row.get("market_id") or row.get("market_slug") or f"row:{index}")


def _side(row: Dict[str, Any]) -> str:
    return str(row.get("side") or row.get("outcome") or "").strip().lower()


def _implied_side_price(row: Dict[str, Any]) -> Optional[float]:
    return _safe_float(row.get("market_probability") if row.get("market_probability") is not None else row.get("price"))


def _yes_price_by_market(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    yes_prices: Dict[str, float] = {}
    no_prices: Dict[str, float] = {}
    for index, row in enumerate(rows):
        key = _market_key(row, index)
        price = _implied_side_price(row)
        if price is None:
            continue
        if _side(row) == "yes":
            yes_prices[key] = _clamp_probability(price)
        elif _side(row) == "no":
            no_prices[key] = _clamp_probability(price)
    for key, price in no_prices.items():
        yes_prices.setdefault(key, _clamp_probability(1.0 - price))
    return yes_prices


def _monotonic_non_decreasing_fit(points: List[Tuple[float, float]]) -> List[float]:
    """Pool adjacent violators algorithm with equal weights."""

    if not points:
        return []
    blocks: List[Dict[str, float]] = []
    for _, value in points:
        blocks.append({"sum": float(value), "weight": 1.0, "start": float(len(blocks)), "end": float(len(blocks))})
        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            if left["sum"] / left["weight"] <= right["sum"] / right["weight"]:
                break
            merged = {
                "sum": left["sum"] + right["sum"],
                "weight": left["weight"] + right["weight"],
                "start": left["start"],
                "end": right["end"],
            }
            blocks[-2:] = [merged]
    fitted: List[float] = []
    for block in blocks:
        average = _clamp_probability(block["sum"] / block["weight"])
        fitted.extend([average] * int(block["end"] - block["start"] + 1))
    return fitted


def _threshold_raw_cdf(bucket_type: str, yes_price: float) -> Optional[float]:
    if bucket_type == "le":
        return _clamp_probability(yes_price)
    if bucket_type == "ge":
        return _clamp_probability(1.0 - yes_price)
    return None


def enrich_payload_with_market_implied(payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    yes_prices = _yes_price_by_market(rows)
    enriched_rows = [dict(row) for row in rows]

    exact_groups: Dict[str, List[Tuple[int, float]]] = {}
    threshold_groups: Dict[str, List[Tuple[int, float, float]]] = {}
    for index, row in enumerate(enriched_rows):
        bucket_type = _bucket_type(row)
        market_key = _market_key(row, index)
        yes_price = yes_prices.get(market_key)
        side_price = _implied_side_price(row)
        if yes_price is None:
            continue
        enriched_rows[index]["market_implied_schema_version"] = SCHEMA_VERSION
        enriched_rows[index]["market_implied_yes_price"] = round(yes_price, 6)
        enriched_rows[index]["market_implied_side_price"] = (
            round(side_price, 6) if side_price is not None else None
        )
        if bucket_type in {"eq", "range"}:
            exact_groups.setdefault(_event_group_key(row), []).append((index, yes_price))
        elif bucket_type in {"le", "ge"}:
            raw_cdf = _threshold_raw_cdf(bucket_type, yes_price)
            threshold = _threshold(row)
            if raw_cdf is None or threshold is None:
                continue
            threshold_groups.setdefault(_event_group_key(row), []).append((index, threshold, raw_cdf))
            enriched_rows[index]["market_implied_cdf_raw"] = round(raw_cdf, 6)

    exact_group_summaries: List[Dict[str, Any]] = []
    for group_key, items in exact_groups.items():
        overround = sum(price for _, price in items)
        status = "normalised" if len(items) >= 2 and overround > 0 else "insufficient_family"
        for index, yes_price in items:
            side = _side(enriched_rows[index])
            normalized_yes = yes_price / overround if overround > 0 else None
            enriched_rows[index]["market_implied_group_key"] = group_key
            enriched_rows[index]["market_implied_bucket_family"] = "mutually_exclusive"
            enriched_rows[index]["market_implied_overround"] = round(overround, 6)
            enriched_rows[index]["market_implied_underround"] = round(max(0.0, 1.0 - overround), 6)
            enriched_rows[index]["market_implied_de_vig_status"] = status
            if normalized_yes is not None:
                normalized_yes = _clamp_probability(normalized_yes)
                enriched_rows[index]["market_implied_de_vig_yes_probability"] = round(normalized_yes, 6)
                enriched_rows[index]["market_implied_de_vig_side_probability"] = round(
                    normalized_yes if side != "no" else 1.0 - normalized_yes,
                    6,
                )
        exact_group_summaries.append(
            {
                "group_key": group_key,
                "bucket_family": "mutually_exclusive",
                "row_count": len(items),
                "overround": round(overround, 6),
                "underround": round(max(0.0, 1.0 - overround), 6),
                "status": status,
            }
        )

    threshold_group_summaries: List[Dict[str, Any]] = []
    for group_key, items in threshold_groups.items():
        ordered = sorted(items, key=lambda item: item[1])
        raw_points = [(threshold, raw_cdf) for _, threshold, raw_cdf in ordered]
        fitted = _monotonic_non_decreasing_fit(raw_points)
        violation_count = sum(
            1
            for left, right in zip(raw_points, raw_points[1:])
            if right[1] + 1e-12 < left[1]
        )
        for (index, threshold, raw_cdf), fitted_cdf in zip(ordered, fitted):
            enriched_rows[index]["market_implied_group_key"] = group_key
            enriched_rows[index]["market_implied_bucket_family"] = "threshold_cdf"
            enriched_rows[index]["market_implied_cdf"] = round(fitted_cdf, 6)
            enriched_rows[index]["market_implied_cdf_threshold"] = threshold
            enriched_rows[index]["market_implied_monotonic_adjustment"] = round(fitted_cdf - raw_cdf, 6)
            enriched_rows[index]["market_implied_monotonic_violation"] = violation_count > 0
        threshold_group_summaries.append(
            {
                "group_key": group_key,
                "bucket_family": "threshold_cdf",
                "row_count": len(items),
                "monotonic_violation_count": violation_count,
                "status": "fitted" if items else "empty",
            }
        )

    diagnostics = dict(payload.get("diagnostics") or {})
    diagnostics["market_implied"] = {
        "schema_version": SCHEMA_VERSION,
        "rows_seen": len(rows),
        "rows_with_yes_price": len(
            [row for row in enriched_rows if row.get("market_implied_yes_price") is not None]
        ),
        "mutually_exclusive_group_count": len(exact_group_summaries),
        "threshold_cdf_group_count": len(threshold_group_summaries),
        "monotonic_violation_group_count": len(
            [row for row in threshold_group_summaries if row.get("monotonic_violation_count")]
        ),
        "mutually_exclusive_groups": exact_group_summaries,
        "threshold_cdf_groups": threshold_group_summaries,
    }
    return {**payload, "rows": enriched_rows, "diagnostics": diagnostics}
