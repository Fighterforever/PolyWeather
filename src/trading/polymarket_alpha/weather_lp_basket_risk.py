from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_basket_risk.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def evaluate_weather_lp_basket(
    *,
    legs: Iterable[Dict[str, Any]],
    city_volatility_bucket: str = "insufficient_data",
    reward_estimate: Optional[float] = None,
    includes_smart_holder_bucket: bool = False,
) -> Dict[str, Any]:
    materialized = [row for row in legs if isinstance(row, dict)]
    costs = [_safe_float(row.get("entry_price") or row.get("quote_price") or row.get("best_ask")) for row in materialized]
    costs = [value for value in costs if value is not None]
    total = round(sum(costs), 8)
    worst_case_payout = 1.0 if materialized else 0.0
    max_loss = round(max(0.0, total - (reward_estimate or 0.0)), 8)
    reward = _safe_float(reward_estimate) or 0.0
    reward_to_risk = round(reward / max(0.0001, total), 8) if total else None
    width = len(materialized)
    blockers: List[str] = []
    decision = "pass"
    if total >= 0.98:
        blockers.append("expensive_basket_near_full_payout")
        decision = "reject"
    elif total >= 0.95:
        blockers.append("expensive_basket_watch_only")
        decision = "watch_only"
    if city_volatility_bucket == "stable" and width > 3:
        blockers.append("stable_city_prefers_narrow_range")
        decision = "watch_only" if decision == "pass" else decision
    if city_volatility_bucket == "volatile" and width > 3 and (reward_to_risk is None or reward_to_risk < 0.02):
        blockers.append("volatile_city_requires_reward_buffer")
        decision = "watch_only" if decision == "pass" else decision
    return {
        "schema_version": SCHEMA_VERSION,
        "legs": materialized,
        "total_entry_cost": total,
        "worst_case_payout": worst_case_payout,
        "max_loss": max_loss,
        "reward_estimate": reward,
        "reward_to_risk_ratio": reward_to_risk,
        "city_volatility_bucket": city_volatility_bucket,
        "basket_width": width,
        "includes_smart_holder_bucket": bool(includes_smart_holder_bucket),
        "expensive_basket_blocker": "expensive_basket_near_full_payout" if total >= 0.98 else None,
        "blockers": blockers,
        "decision": decision,
        "reward_counted_as_guaranteed_pnl": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_basket_risk_report(baskets: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [row for row in baskets if isinstance(row, dict)]
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "basket_count": len(rows),
        "expensive_basket_rejection_count": len([row for row in rows if row.get("expensive_basket_blocker")]),
        "watch_only_count": len([row for row in rows if row.get("decision") == "watch_only"]),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "baskets": rows,
    }


__all__ = ["SCHEMA_VERSION", "evaluate_weather_lp_basket", "build_basket_risk_report"]
