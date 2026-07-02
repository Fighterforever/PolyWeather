from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from src.trading.polymarket_alpha.live_safety import evaluate_live_safety, load_live_safety_config
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_tiny_live_selector.v1"
STRATEGY_ID = "weather_lp_reward"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def load_jsonl_or_json(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    text = source.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if isinstance(parsed, dict):
        rows = parsed.get("selected_quotes") or parsed.get("rows") or parsed.get("recommended_quote_subset")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _by_market_token(rows: Iterable[Dict[str, Any]]) -> Dict[tuple[str, str], Dict[str, Any]]:
    indexed: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("market_slug") or ""), str(row.get("token_id") or ""))
        if key[0] and key[1]:
            indexed[key] = row
    return indexed


def _client_order_id(row: Dict[str, Any]) -> str:
    raw = "|".join(
        [
            STRATEGY_ID,
            str(row.get("market_slug") or ""),
            str(row.get("token_id") or ""),
            str(row.get("suggested_quote_price") or ""),
            str(row.get("rank") or ""),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:18]
    return f"wlp-tiny-{digest}"


def _side_action(side: Any) -> str:
    # Weather LP paper quotes represent owning a low-risk outcome token, so the
    # audit order is a BUY resting limit. Do not infer any sell/taker action.
    return "BUY"


def _expiration_ts(now: datetime, max_age_minutes: int = 30) -> int:
    return int((now + timedelta(minutes=max_age_minutes)).timestamp())


def _capital(row: Dict[str, Any]) -> float:
    capital = _safe_float(row.get("capital_at_risk"))
    if capital is not None:
        return capital
    price = _safe_float(row.get("suggested_quote_price")) or 0.0
    size = _safe_float(row.get("suggested_quote_size")) or 0.0
    return round(price * size, 8)


def _row_blockers(row: Dict[str, Any], impact: Dict[str, Any], kill_switch_ready: bool) -> List[str]:
    blockers: List[str] = []
    price = _safe_float(row.get("suggested_quote_price"))
    size = _safe_float(row.get("suggested_quote_size"))
    min_size = _safe_float(row.get("min_incentive_size"))
    max_spread = _safe_float(row.get("max_incentive_spread"))
    distance = _safe_float(row.get("distance_from_midpoint") or row.get("quote_distance_from_midpoint"))
    if not row.get("market_slug"):
        blockers.append("missing_market_slug")
    if row.get("strategy_id") and str(row.get("strategy_id")) != STRATEGY_ID:
        blockers.append("strategy_not_weather_lp_reward")
    if not row.get("token_id"):
        blockers.append("missing_token_id")
    if price is None:
        blockers.append("missing_price")
    if size is None or size <= 0:
        blockers.append("missing_size")
    if min_size is None:
        blockers.append("missing_min_incentive_size")
    elif size is not None and size < min_size:
        blockers.append("min_incentive_size_not_met")
    if max_spread is None:
        blockers.append("missing_max_incentive_spread")
    if distance is not None and max_spread is not None and distance > max_spread:
        blockers.append("spread_from_midpoint_exceeds_max_incentive_spread")
    if not impact:
        blockers.append("missing_impact_simulation")
    if impact and impact.get("quote_would_cross_or_take"):
        blockers.append("quote_would_cross_or_take")
    if impact and impact.get("quote_would_be_resting") is False:
        blockers.append("quote_would_not_be_resting")
    if impact and impact.get("qualifies_for_reward_after_insert") is False:
        blockers.append("not_reward_qualified_after_insert")
    if impact and impact.get("impact_simulation_pass") is False:
        blockers.append("impact_simulation_failed")
    if not row.get("cancel_rules") and not row.get("cancellation_rules"):
        blockers.append("missing_cancellation_rule")
    if not kill_switch_ready:
        blockers.append("kill_switch_not_ready")
    if _safe_float(row.get("visible_reward_share_proxy")) is None:
        blockers.append("missing_visible_reward_share_proxy")
    if _safe_float(row.get("basket_total_cost")) is not None and float(row.get("basket_total_cost")) >= 0.98:
        blockers.append("expensive_basket_near_full_payout")
    return blockers


def build_weather_lp_tiny_live_selected_orders(
    *,
    selected_quote_report: Dict[str, Any],
    impact_rows: Iterable[Dict[str, Any]] = (),
    kill_switch_checklist: Dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    now = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    config = load_live_safety_config(env or os.environ)
    quote_rows = selected_quote_report.get("selected_quotes") or []
    if not isinstance(quote_rows, list):
        quote_rows = []
    impact_by_key = _by_market_token(impact_rows)
    kill_switch_ready = bool((kill_switch_checklist or {}).get("ready") or (kill_switch_checklist or {}).get("checklist_status") == "ready")
    selected_orders: List[Dict[str, Any]] = []
    rejected_rows: List[Dict[str, Any]] = []
    blockers = Counter()
    market_capital: defaultdict[str, float] = defaultdict(float)
    city_capital: defaultdict[str, float] = defaultdict(float)
    total_capital = 0.0
    max_orders = min(_safe_int(config.get("max_open_orders"), 3), 3)
    for row in quote_rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("market_slug") or ""), str(row.get("token_id") or ""))
        impact = impact_by_key.get(key, {})
        row_blockers = _row_blockers(row, impact, kill_switch_ready)
        capital = _capital(row)
        market_slug = key[0]
        city = str(row.get("city") or "")
        if len(selected_orders) >= max_orders:
            row_blockers.append("max_open_order_selection_limit_reached")
        if total_capital + capital > float(config["max_total_capital_usd"]):
            row_blockers.append("max_total_capital_exceeded")
        if market_capital[market_slug] + capital > float(config["max_per_market_usd"]):
            row_blockers.append("max_per_market_exceeded")
        if city_capital[city] + capital > float(config["max_per_city_usd"]):
            row_blockers.append("max_per_city_exceeded")
        price = _safe_float(row.get("suggested_quote_price"))
        best_bid = _safe_float(row.get("current_best_bid") or impact.get("current_best_bid"))
        best_ask = _safe_float(row.get("current_best_ask") or impact.get("current_best_ask"))
        safety = evaluate_live_safety(
            order={"strategy_id": STRATEGY_ID, "order_type": "GTD", "action": _side_action(row.get("side")), "price": price},
            env=env or os.environ,
            balance_available=True,
            wallet_balance_check_passed=True,
            open_order_count=len(selected_orders) + 1,
            total_capital_usd=total_capital + capital,
            per_market_capital_usd=market_capital[market_slug] + capital,
            per_city_capital_usd=city_capital[city] + capital,
            selected_quote_allowlisted=True,
            reward_qualified=not any(reason.startswith("not_reward_qualified") for reason in row_blockers),
            best_bid=best_bid,
            best_ask=best_ask,
        )
        # The selector is allowed to build candidates while env flags are false.
        # Operational flags are enforced by the runner before placement.
        operational_blockers = {
            "credentials_missing",
            "global_live_disabled",
            "strategy_live_disabled",
            "balance_unavailable",
            "wallet_balance_check_failed",
        }
        row_blockers.extend([reason for reason in safety.get("safety_blockers") or [] if reason not in operational_blockers])
        row_blockers = list(dict.fromkeys(row_blockers))
        if row_blockers:
            for reason in row_blockers:
                blockers[reason] += 1
            rejected_rows.append(
                {
                    "selected_quote_id": row.get("rank") or row.get("selected_quote_id"),
                    "market_slug": market_slug,
                    "token_id": key[1],
                    "city": city,
                    "capital_at_risk": capital,
                    "blockers": row_blockers,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
            continue
        selected_quote_id = str(row.get("selected_quote_id") or f"manual_audit_quote_{row.get('rank') or len(selected_orders) + 1}")
        order = {
            "schema_version": f"{SCHEMA_VERSION}.order.v1",
            "selected_quote_id": selected_quote_id,
            "client_order_id": _client_order_id(row),
            "strategy_id": STRATEGY_ID,
            "market_slug": market_slug,
            "token_id": key[1],
            "city": city,
            "side": row.get("side"),
            "action": _side_action(row.get("side")),
            "price": price,
            "size": _safe_float(row.get("suggested_quote_size")),
            "expiration_ts": _expiration_ts(now),
            "order_type": "GTD",
            "post_only_or_resting_required": True,
            "max_loss": capital,
            "capital_at_risk": capital,
            "cancellation_rules": row.get("cancel_rules") or row.get("cancellation_rules"),
            "reward_score_proxy": row.get("expected_reward_base"),
            "visible_reward_share_proxy": row.get("visible_reward_share_proxy") or impact.get("post_visible_share_proxy"),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "min_incentive_size": row.get("min_incentive_size"),
            "max_incentive_spread": row.get("max_incentive_spread"),
            "impact_simulation_pass": True,
            "kill_switch_rule_attached": True,
            "live_order_path_candidate": True,
            "requires_safety_gate": True,
            "manual_review_required": True,
            "paper_only": False,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        selected_orders.append(order)
        total_capital += capital
        market_capital[market_slug] += capital
        city_capital[city] += capital
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "input_selected_quote_count": len(quote_rows),
        "selected_order_count": len(selected_orders),
        "selected_total_capital_at_risk": round(total_capital, 8),
        "max_total_capital_limit": config["max_total_capital_usd"],
        "max_per_market_limit": config["max_per_market_usd"],
        "max_per_city_limit": config["max_per_city_usd"],
        "max_open_orders": max_orders,
        "rejected_order_count": len(rejected_rows),
        "blocker_counts": [{"reason": reason, "count": count} for reason, count in sorted(blockers.items())],
        "selected_orders": selected_orders,
        "rejected_orders": rejected_rows,
        "selector_only_no_order_placement": True,
        "requires_safety_gate": True,
        "live_order_path": False,
    }


def write_selected_orders_csv(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    columns = [
        "selected_quote_id",
        "client_order_id",
        "strategy_id",
        "market_slug",
        "token_id",
        "city",
        "side",
        "action",
        "price",
        "size",
        "expiration_ts",
        "order_type",
        "capital_at_risk",
        "visible_reward_share_proxy",
        "live_order_path_candidate",
        "requires_safety_gate",
        "live_order_path",
    ]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in materialized:
            writer.writerow({key: row.get(key) for key in columns})
    return len(materialized)


__all__ = [
    "SCHEMA_VERSION",
    "STRATEGY_ID",
    "build_weather_lp_tiny_live_selected_orders",
    "load_jsonl_or_json",
    "utc_now_iso",
    "write_json",
    "write_jsonl",
    "write_selected_orders_csv",
]
