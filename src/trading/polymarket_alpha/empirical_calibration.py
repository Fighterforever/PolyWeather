from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from src.trading.polymarket_alpha.probability_dataset import load_jsonl, write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_empirical_calibration.v1"


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _log_loss(p: float, y: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, float(p)))
    return -(float(y) * math.log(p) + (1.0 - float(y)) * math.log(1.0 - p))


def _group_key(row: Dict[str, Any], fields: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(str(row.get(field) or "missing") for field in fields)


def _calibration_row(rows: List[Dict[str, Any]], fields: Tuple[str, ...], key: Tuple[str, ...], *, min_sample_count: int) -> Dict[str, Any]:
    prices = [_safe_float(row.get("price_mid")) for row in rows]
    payouts = [_safe_float(row.get("resolved_payout")) for row in rows]
    pairs = [(p, y) for p, y in zip(prices, payouts) if p is not None and y is not None]
    sample_count = len(pairs)
    mean_price = sum(p for p, _ in pairs) / sample_count if sample_count else None
    realized = sum(y for _, y in pairs) / sample_count if sample_count else None
    brier = sum((p - y) ** 2 for p, y in pairs) / sample_count if sample_count else None
    log_loss = sum(_log_loss(p, y) for p, y in pairs) / sample_count if sample_count else None
    payload = {
        field: value for field, value in zip(fields, key)
    }
    payload.update(
        {
            "group_fields": list(fields),
            "sample_count": sample_count,
            "mean_market_price": round(mean_price, 8) if mean_price is not None else None,
            "realized_frequency": round(realized, 8) if realized is not None else None,
            "brier_market": round(brier, 8) if brier is not None else None,
            "log_loss_market": round(log_loss, 8) if log_loss is not None else None,
            "calibration_error": round(realized - mean_price, 8) if realized is not None and mean_price is not None else None,
            "meets_min_sample": sample_count >= int(min_sample_count),
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    )
    return payload


def build_empirical_calibration_report(rows: Iterable[Dict[str, Any]], *, min_sample_count: int = 50) -> Dict[str, Any]:
    input_rows = [row for row in rows if isinstance(row, dict)]
    resolved = [
        row for row in input_rows
        if row.get("resolved") and _safe_float(row.get("price_mid")) is not None and _safe_float(row.get("resolved_payout")) is not None
    ]
    group_specs = [
        ("category",),
        ("category", "price_bucket"),
        ("category", "price_bucket", "time_to_close_bucket"),
        ("category", "price_bucket", "spread_bucket"),
        ("category", "price_bucket", "liquidity_bucket"),
    ]
    table: List[Dict[str, Any]] = []
    for fields in group_specs:
        grouped: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
        for row in resolved:
            grouped[_group_key(row, fields)].append(row)
        for key, members in sorted(grouped.items()):
            table.append(_calibration_row(members, fields, key, min_sample_count=min_sample_count))
    significant = sorted(
        [row for row in table if row.get("meets_min_sample") and row.get("calibration_error") is not None],
        key=lambda row: abs(float(row.get("calibration_error") or 0.0)),
        reverse=True,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "input_row_count": len(input_rows),
        "resolved_input_row_count": len(resolved),
        "min_sample_count": int(min_sample_count),
        "group_count": len(table),
        "eligible_group_count": len([row for row in table if row.get("meets_min_sample")]),
        "top_miscalibrated_groups": significant[:25],
        "table": table,
    }


__all__ = ["SCHEMA_VERSION", "build_empirical_calibration_report", "load_jsonl", "write_json", "write_jsonl"]
