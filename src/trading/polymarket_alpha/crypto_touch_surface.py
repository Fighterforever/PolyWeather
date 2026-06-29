from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_touch_surface.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _market_yes_probability(row: Dict[str, Any]) -> Optional[float]:
    market_price = _safe_float(row.get("market_price"))
    if market_price is not None:
        return _clamp_probability(market_price)
    best_ask = _safe_float(row.get("best_ask"))
    spread = _safe_float(row.get("spread"))
    side = str(row.get("side") or "YES").upper()
    if best_ask is None:
        return None
    side_mid = best_ask - (spread or 0.0) / 2.0
    if side == "NO":
        return _clamp_probability(1.0 - side_mid)
    return _clamp_probability(side_mid)


def _isotonic_nonincreasing(values: List[float]) -> List[float]:
    if not values:
        return []
    blocks: List[Dict[str, float]] = []
    for value in values:
        blocks.append({"sum": float(value), "weight": 1.0})
        while len(blocks) >= 2:
            left = blocks[-2]["sum"] / blocks[-2]["weight"]
            right = blocks[-1]["sum"] / blocks[-1]["weight"]
            if left >= right:
                break
            merged = {
                "sum": blocks[-2]["sum"] + blocks[-1]["sum"],
                "weight": blocks[-2]["weight"] + blocks[-1]["weight"],
            }
            blocks[-2:] = [merged]
    fitted: List[float] = []
    for block in blocks:
        fitted.extend([block["sum"] / block["weight"]] * int(block["weight"]))
    return [_clamp_probability(value) for value in fitted]


def _robust_z_scores(values: List[float]) -> List[float]:
    if not values:
        return []
    center = median(values)
    deviations = [abs(value - center) for value in values]
    mad = median(deviations) or 1e-9
    return [round((value - center) / (1.4826 * mad), 6) for value in values]


def _source_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in ("candidates", "near_misses", "near_miss_watch", "top_10_near_misses"):
        for row in report.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _row_key(row: Dict[str, Any]) -> str:
    return "|".join(str(row.get(field) or "") for field in ("market_slug", "token_id", "side"))


def build_crypto_touch_surface_report(
    *,
    crypto_probability_report: Dict[str, Any],
    min_edge: float = 0.01,
) -> Dict[str, Any]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in _source_rows(crypto_probability_report):
        if not (
            row.get("semantics_type") == "touch_barrier"
            and row.get("probability_semantics") == "touch_barrier"
            and row.get("high_since_start_verified") is True
            and row.get("barrier_already_touched") is False
        ):
            continue
        if str(row.get("side") or "").upper() != "YES":
            continue
        threshold = _safe_float(row.get("threshold"))
        market_probability = _market_yes_probability(row)
        model_probability = _safe_float(row.get("p_yes_touch") or row.get("p_yes_model") or row.get("p_model"))
        if threshold is None or market_probability is None or model_probability is None:
            continue
        by_key[_row_key(row)] = row

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in by_key.values():
        group_id = "|".join(
            str(row.get(field) or "")
            for field in ("asset", "target_time", "market_creation_time")
        )
        groups[group_id].append(row)

    surface_rows: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    shadow_rows: List[Dict[str, Any]] = []
    total_violations = 0
    for group_id, members in sorted(groups.items()):
        sorted_rows = sorted(members, key=lambda row: float(row.get("threshold") or 0.0))
        market_probs = [_market_yes_probability(row) or 0.0 for row in sorted_rows]
        fitted = _isotonic_nonincreasing(market_probs)
        residuals = [
            (_safe_float(row.get("p_yes_touch") or row.get("p_yes_model") or row.get("p_model")) or 0.0) - market_probs[index]
            for index, row in enumerate(sorted_rows)
        ]
        z_scores = _robust_z_scores(residuals)
        violation_flags = [False] * len(sorted_rows)
        for index in range(1, len(sorted_rows)):
            if market_probs[index] > market_probs[index - 1] + 1e-9:
                total_violations += 1
                violation_flags[index - 1] = True
                violation_flags[index] = True
        for index, row in enumerate(sorted_rows):
            model_probability = _safe_float(row.get("p_yes_touch") or row.get("p_yes_model") or row.get("p_model")) or 0.0
            market_probability = market_probs[index]
            surface_probability = fitted[index]
            residual = model_probability - market_probability
            surface_residual = market_probability - surface_probability
            ev_safe = _safe_float(row.get("EV_safe"))
            residual_supports_yes = residual > 0 and surface_residual <= 0.005
            no_monotonic_contradiction = not violation_flags[index]
            base = {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "group_id": group_id,
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
                "asset": row.get("asset"),
                "threshold": row.get("threshold"),
                "target_time": row.get("target_time"),
                "market_creation_time": row.get("market_creation_time"),
                "market_yes_probability": round(market_probability, 8),
                "model_yes_probability": round(model_probability, 8),
                "fitted_surface_probability": round(surface_probability, 8),
                "residual": round(residual, 8),
                "surface_residual": round(surface_residual, 8),
                "residual_z_score": z_scores[index] if index < len(z_scores) else None,
                "monotonic_violation": violation_flags[index],
                "EV_safe": ev_safe,
                "surface_supports_model_direction": residual_supports_yes and no_monotonic_contradiction,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
            if (
                ev_safe is not None
                and ev_safe >= float(min_edge)
                and residual_supports_yes
                and no_monotonic_contradiction
            ):
                candidates.append(
                    {
                        **base,
                        "strategy_id": "crypto_touch_barrier_surface_relative_value",
                        "candidate_type": "formal_surface_supported",
                    }
                )
            elif ev_safe is not None and ev_safe >= 0 and abs(float(base["residual_z_score"] or 0.0)) >= 1.0:
                shadow_rows.append(
                    {
                        **base,
                        "strategy_id": "crypto_touch_barrier_surface_shadow_watch",
                        "candidate_type": "shadow_relative_value_watch",
                        "counts_for_live_gate": False,
                    }
                )
            surface_rows.append(base)

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "group_count": len(groups),
        "market_count": len(surface_rows),
        "monotonic_violation_count": total_violations,
        "surface_near_miss_count": len(shadow_rows),
        "relative_value_candidate_count": len(candidates),
        "top_relative_value_rows": sorted(candidates, key=lambda row: float(row.get("EV_safe") or -1e9), reverse=True)[:10],
        "shadow_relative_value_rows": sorted(shadow_rows, key=lambda row: abs(float(row.get("residual_z_score") or 0.0)), reverse=True)[:25],
        "rows": surface_rows,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "SCHEMA_VERSION",
    "build_crypto_touch_surface_report",
    "load_json",
    "write_json",
    "write_jsonl",
]
