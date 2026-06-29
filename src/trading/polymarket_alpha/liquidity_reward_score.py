from __future__ import annotations

import math
from typing import Any, Dict, Optional


SCHEMA_VERSION = "polyweather_polymarket_alpha_liquidity_reward_score.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def order_score(max_incentive_spread: Any, spread_from_midpoint: Any, *, size: Any, min_incentive_size: Any) -> float:
    v = _safe_float(max_incentive_spread)
    s = _safe_float(spread_from_midpoint)
    qty = _safe_float(size)
    minimum = _safe_float(min_incentive_size)
    if v is None or v <= 0 or s is None or s < 0 or qty is None or minimum is None:
        return 0.0
    if s > v or qty < minimum:
        return 0.0
    base = ((v - s) / v) ** 2
    return round(base * qty, 8)


def compute_liquidity_reward_score(
    *,
    midpoint: Any,
    order_price: Any,
    order_size: Any,
    side: str,
    max_incentive_spread: Any,
    min_incentive_size: Any,
    complement_order_price: Any = None,
    complement_order_size: Any = None,
    single_sided_adjustment_factor: float = 3.0,
) -> Dict[str, Any]:
    mid = _safe_float(midpoint)
    price = _safe_float(order_price)
    size = _safe_float(order_size)
    v = _safe_float(max_incentive_spread)
    minimum = _safe_float(min_incentive_size)
    side_key = str(side or "").lower()
    blockers: list[str] = []
    if mid is None:
        blockers.append("missing_midpoint")
    if price is None:
        blockers.append("missing_order_price")
    if size is None:
        blockers.append("missing_order_size")
    if v is None or v <= 0:
        blockers.append("missing_max_incentive_spread")
    if minimum is None or minimum <= 0:
        blockers.append("missing_min_incentive_size")

    spread = abs(float(price) - float(mid)) if price is not None and mid is not None else None
    q_one_raw = order_score(v, spread, size=size, min_incentive_size=minimum)
    complement_spread = None
    q_two = 0.0
    if complement_order_price is not None:
        comp_price = _safe_float(complement_order_price)
        comp_size = _safe_float(complement_order_size)
        if comp_price is not None and mid is not None:
            complement_spread = abs(float(comp_price) - float(mid))
        q_two = order_score(v, complement_spread, size=comp_size, min_incentive_size=minimum)

    midpoint_extreme = bool(mid is not None and not (0.10 <= float(mid) <= 0.90))
    single_sided_discount_applied = bool(q_one_raw > 0 and q_two <= 0 and not midpoint_extreme)
    if midpoint_extreme and q_two <= 0:
        q_one = 0.0
        if q_one_raw > 0:
            blockers.append("extreme_midpoint_requires_two_sided_liquidity")
    elif single_sided_discount_applied:
        q_one = round(q_one_raw / max(1.0, float(single_sided_adjustment_factor)), 8)
    else:
        q_one = q_one_raw
    if q_one_raw <= 0 and "missing_order_price" not in blockers:
        blockers.append("order_does_not_qualify_for_reward_score")

    q_min = min(q_one, q_two) if q_two > 0 else q_one
    normalized = q_min / max(float(minimum or 1.0), 1.0) if q_min > 0 else 0.0
    qualifies = bool(q_min > 0 and not blockers)
    if blockers == ["order_does_not_qualify_for_reward_score"] and q_min > 0:
        blockers = []
        qualifies = True
    return {
        "schema_version": SCHEMA_VERSION,
        "side": side_key,
        "midpoint": mid,
        "order_price": price,
        "spread_from_midpoint": round(float(spread), 8) if spread is not None else None,
        "max_incentive_spread": v,
        "min_incentive_size": minimum,
        "order_size": size,
        "q_one": round(q_one, 8),
        "q_two": round(q_two, 8),
        "q_min": round(q_min, 8),
        "normalized_score_proxy": round(normalized, 8),
        "single_sided_discount_applied": single_sided_discount_applied,
        "qualifies_for_reward": qualifies,
        "blockers": blockers,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


__all__ = ["SCHEMA_VERSION", "compute_liquidity_reward_score", "order_score"]
