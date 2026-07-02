from __future__ import annotations

import os
from typing import Any, Dict, Mapping


SCHEMA_VERSION = "polyweather_polymarket_alpha_live_safety.v1"


def _env_bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    value = str(env.get(key, str(default))).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(float(env.get(key, default)))
    except (TypeError, ValueError):
        return default


def _allowlist(env: Mapping[str, str], key: str, default: str) -> list[str]:
    raw = str(env.get(key, default) or "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def credentials_present(env: Mapping[str, str] | None = None) -> bool:
    env = env or os.environ
    required = ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET", "POLYMARKET_PASSPHRASE")
    return all(bool(str(env.get(key) or "").strip()) for key in required)


def load_live_safety_config(env: Mapping[str, str] | None = None) -> Dict[str, Any]:
    env = env or os.environ
    return {
        "schema_version": SCHEMA_VERSION,
        "global_live_enabled": _env_bool(env, "POLYWEATHER_ENABLE_LIVE", False),
        "strategy_live_enabled": _env_bool(env, "POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE", False),
        "strategy_allowlist": _allowlist(env, "POLYWEATHER_LIVE_STRATEGY_ALLOWLIST", "weather_lp_reward"),
        "max_total_capital_usd": _env_float(env, "POLYWEATHER_MAX_TOTAL_CAPITAL_USD", 25.0),
        "max_per_market_usd": _env_float(env, "POLYWEATHER_MAX_PER_MARKET_USD", 5.0),
        "max_per_city_usd": _env_float(env, "POLYWEATHER_MAX_PER_CITY_USD", 10.0),
        "max_open_orders": _env_int(env, "POLYWEATHER_MAX_OPEN_ORDERS", 3),
        "daily_stop_loss_usd": _env_float(env, "POLYWEATHER_DAILY_STOP_LOSS_USD", 10.0),
        "require_resting_only": _env_bool(env, "POLYWEATHER_REQUIRE_RESTING_ONLY", True),
        "allow_taker": _env_bool(env, "POLYWEATHER_ALLOW_TAKER", False),
        "order_type_allowlist": _allowlist(env, "POLYWEATHER_ORDER_TYPE_ALLOWLIST", "GTD"),
        "live_order_path_default": False,
    }


def _would_cross(*, action: str, price: float | None, best_bid: float | None, best_ask: float | None) -> bool:
    side = str(action or "BUY").strip().upper()
    if price is None:
        return True
    if side == "SELL":
        return best_bid is not None and price <= best_bid
    return best_ask is not None and price >= best_ask


def evaluate_live_safety(
    *,
    order: Dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    credentials_present_override: bool | None = None,
    balance_available: bool = False,
    wallet_balance_check_passed: bool = False,
    open_order_count: int = 0,
    total_capital_usd: float = 0.0,
    per_market_capital_usd: float = 0.0,
    per_city_capital_usd: float = 0.0,
    selected_quote_allowlisted: bool = True,
    reward_qualified: bool = True,
    best_bid: float | None = None,
    best_ask: float | None = None,
) -> Dict[str, Any]:
    env = env or os.environ
    order = order or {}
    config = load_live_safety_config(env)
    strategy_id = str(order.get("strategy_id") or "")
    order_type = str(order.get("order_type") or "").upper()
    action = str(order.get("action") or order.get("order_side") or "BUY").upper()
    try:
        price = float(order.get("price"))
    except (TypeError, ValueError):
        price = None
    cap_total = float(total_capital_usd or 0.0)
    cap_market = float(per_market_capital_usd or 0.0)
    cap_city = float(per_city_capital_usd or 0.0)
    strategy_allowlisted = strategy_id in config["strategy_allowlist"]
    creds = credentials_present_override if credentials_present_override is not None else credentials_present(env)
    crossing = _would_cross(action=action, price=price, best_bid=best_bid, best_ask=best_ask)
    blockers: list[str] = []
    if not config["global_live_enabled"]:
        blockers.append("global_live_disabled")
    if not config["strategy_live_enabled"]:
        blockers.append("strategy_live_disabled")
    if not strategy_allowlisted:
        blockers.append("strategy_not_allowlisted")
    if not creds:
        blockers.append("credentials_missing")
    if not balance_available:
        blockers.append("balance_unavailable")
    if not wallet_balance_check_passed:
        blockers.append("wallet_balance_check_failed")
    if cap_total > config["max_total_capital_usd"]:
        blockers.append("max_total_capital_exceeded")
    if cap_market > config["max_per_market_usd"]:
        blockers.append("max_per_market_exceeded")
    if cap_city > config["max_per_city_usd"]:
        blockers.append("max_per_city_exceeded")
    if open_order_count > config["max_open_orders"]:
        blockers.append("max_open_orders_exceeded")
    if order_type not in config["order_type_allowlist"]:
        blockers.append("order_type_not_allowlisted")
    if order_type in {"MARKET", "FOK", "FAK"}:
        blockers.append("forbidden_order_type")
    if crossing and config["require_resting_only"]:
        blockers.append("order_would_cross_spread")
    if crossing and not config["allow_taker"]:
        blockers.append("taker_not_allowed")
    if not reward_qualified:
        blockers.append("quote_not_reward_qualified")
    if not selected_quote_allowlisted:
        blockers.append("selected_quote_not_allowlisted")
    safety_passed = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "global_live_enabled": config["global_live_enabled"],
        "strategy_live_enabled": config["strategy_live_enabled"],
        "strategy_allowlisted": strategy_allowlisted,
        "credentials_present": bool(creds),
        "balance_available": bool(balance_available),
        "wallet_balance_check_passed": bool(wallet_balance_check_passed),
        "max_total_capital_usd": config["max_total_capital_usd"],
        "max_per_market_usd": config["max_per_market_usd"],
        "max_per_city_usd": config["max_per_city_usd"],
        "max_open_orders": config["max_open_orders"],
        "daily_stop_loss_usd": config["daily_stop_loss_usd"],
        "allow_taker": config["allow_taker"],
        "require_resting_only": config["require_resting_only"],
        "order_type_allowlist": config["order_type_allowlist"],
        "order_would_cross_spread": crossing,
        "selected_quote_allowlisted": selected_quote_allowlisted,
        "reward_qualified": reward_qualified,
        "safety_blockers": list(dict.fromkeys(blockers)),
        "safety_passed": safety_passed,
        "live_order_path": safety_passed,
    }


__all__ = ["SCHEMA_VERSION", "credentials_present", "evaluate_live_safety", "load_live_safety_config"]
