from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional


LIQUIDITY_REWARD_KEYS = {
    "min_incentive_size",
    "max_incentive_spread",
    "rewardsMinSize",
    "rewardsMaxSpread",
    "rewards_min_size",
    "rewards_max_spread",
}
MAKER_REBATE_KEYS = {
    "maker_base_fee",
    "makerBaseFee",
    "taker_base_fee",
    "takerBaseFee",
    "feeSchedule",
    "feeType",
    "feesEnabled",
}


def safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def normalize_reward_spread(value: Any) -> Optional[float]:
    parsed = safe_float(value)
    if parsed is None:
        return None
    if parsed > 1.0:
        return round(parsed / 100.0, 8)
    return round(parsed, 8)


def raw_reward_field_names(payload: Dict[str, Any]) -> List[str]:
    names: set[str] = set()
    for key in payload:
        if "reward" in key.lower() or "incentive" in key.lower() or "fee" in key.lower():
            names.add(key)
    rewards = payload.get("rewards")
    if isinstance(rewards, dict):
        for key in rewards:
            names.add(f"rewards.{key}")
    return sorted(names)


def _first_present(payload: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def extract_liquidity_reward_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    rewards = payload.get("rewards") if isinstance(payload.get("rewards"), dict) else {}
    min_size = _first_present(
        payload,
        ("min_incentive_size", "rewardsMinSize", "rewards_min_size", "rewards_minimum_size"),
    )
    max_spread = _first_present(
        payload,
        ("max_incentive_spread", "rewardsMaxSpread", "rewards_max_spread", "rewards_maximum_spread"),
    )
    if min_size is None and rewards:
        min_size = _first_present(rewards, ("min_size", "minSize", "rewardsMinSize"))
    if max_spread is None and rewards:
        max_spread = _first_present(rewards, ("max_spread", "maxSpread", "rewardsMaxSpread"))

    reward_allocation = _first_present(
        payload,
        ("reward_allocation", "rewardAllocation", "rewardsDailyRate", "rewards_daily_rate", "liquidityReward"),
    )
    if reward_allocation is None and rewards:
        reward_allocation = _first_present(rewards, ("rates", "rate", "dailyRate", "allocation"))

    min_size_float = safe_float(min_size)
    max_spread_float = normalize_reward_spread(max_spread)
    field_names = raw_reward_field_names(payload)
    has_liquidity_fields = min_size_float is not None or max_spread_float is not None
    has_positive_liquidity_fields = bool((min_size_float or 0.0) > 0 and (max_spread_float or 0.0) > 0)
    has_maker_fields = any(key in payload for key in MAKER_REBATE_KEYS)

    if has_positive_liquidity_fields:
        reward_program_type = "liquidity_reward"
        gap_reason = None
    elif has_liquidity_fields:
        reward_program_type = "unknown"
        gap_reason = "not_reward_eligible"
    elif has_maker_fields:
        reward_program_type = "maker_rebate"
        gap_reason = "maker_rebate_only_not_liquidity_reward"
    else:
        reward_program_type = "unknown"
        gap_reason = "no_reward_fields_in_response"

    return {
        "feesEnabled": payload.get("feesEnabled") if "feesEnabled" in payload else payload.get("fees_enabled"),
        "min_incentive_size": min_size_float,
        "max_incentive_spread": max_spread_float,
        "max_incentive_spread_raw": safe_float(max_spread),
        "reward_allocation": reward_allocation,
        "reward_program_type": reward_program_type,
        "raw_field_names_found": field_names,
        "gap_reason": gap_reason,
    }


__all__ = [
    "LIQUIDITY_REWARD_KEYS",
    "MAKER_REBATE_KEYS",
    "extract_liquidity_reward_metadata",
    "normalize_reward_spread",
    "raw_reward_field_names",
    "safe_float",
]
