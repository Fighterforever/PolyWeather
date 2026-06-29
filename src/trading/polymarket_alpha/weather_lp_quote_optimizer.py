from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.liquidity_reward_score import compute_liquidity_reward_score
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_alpha.weather_lp_basket_risk import evaluate_weather_lp_basket


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_quote_optimizer.v1"
VARIANTS = (
    "single_sided_tight",
    "single_sided_conservative",
    "dual_sided_balanced",
    "dual_sided_reward_max",
    "low_capital_single_leg",
    "hour_boundary_cancel_variant",
    "reward_window_variant",
)


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _midpoint(row: Dict[str, Any]) -> Optional[float]:
    bid = _safe_float(row.get("best_bid") or row.get("yes_best_bid"))
    ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 8)


def _quote_price(row: Dict[str, Any], *, midpoint: Optional[float], variant: str) -> Optional[float]:
    bid = _safe_float(row.get("best_bid") or row.get("yes_best_bid"))
    ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
    max_spread = _safe_float(row.get("max_incentive_spread")) or 0.01
    if bid is None or ask is None or midpoint is None or ask <= bid:
        return None
    if variant in {"single_sided_tight", "dual_sided_reward_max", "reward_window_variant"}:
        offset = min(max_spread * 0.10, max((ask - bid) * 0.20, 0.001))
    elif variant in {"single_sided_conservative", "hour_boundary_cancel_variant"}:
        offset = min(max_spread * 0.50, max((ask - bid) * 0.40, 0.001))
    elif variant == "low_capital_single_leg":
        offset = min(max_spread * 0.75, max((ask - bid) * 0.50, 0.001))
    else:
        offset = min(max_spread * 0.25, max((ask - bid) * 0.25, 0.001))
    price = max(bid + 0.001, min(midpoint - offset, ask - 0.001))
    if not (0 < price < 1):
        return None
    return round(price, 4)


def _no_quote_price(row: Dict[str, Any], *, midpoint: Optional[float], variant: str) -> Optional[float]:
    bid = _safe_float(row.get("no_best_bid"))
    ask = _safe_float(row.get("no_best_ask"))
    if bid is None or ask is None or midpoint is None or ask <= bid:
        return None
    no_mid = 1.0 - midpoint
    max_spread = _safe_float(row.get("max_incentive_spread")) or 0.01
    offset = min(max_spread * (0.10 if "reward_max" in variant else 0.25), max((ask - bid) * 0.25, 0.001))
    price = max(bid + 0.001, min(no_mid - offset, ask - 0.001))
    if not (0 < price < 1):
        return None
    return round(price, 4)


def build_weather_lp_quote_optimizer(
    *,
    reward_markets: Iterable[Dict[str, Any]],
    city_regimes: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    markets = [row for row in reward_markets if isinstance(row, dict)]
    regime_by_city = {str(row.get("city") or "").lower(): row for row in city_regimes if isinstance(row, dict)}
    rows: List[Dict[str, Any]] = []
    selected_by_market: Dict[str, Dict[str, Any]] = {}
    for market in markets:
        city = str(market.get("city") or "").lower()
        regime = regime_by_city.get(city) or {}
        midpoint = _midpoint(market)
        min_size = _safe_float(market.get("min_incentive_size"))
        max_spread = _safe_float(market.get("max_incentive_spread"))
        for variant in VARIANTS:
            quote_price = _quote_price(market, midpoint=midpoint, variant=variant)
            no_quote = _no_quote_price(market, midpoint=midpoint, variant=variant) if variant.startswith("dual") else None
            size_multiplier = 0.5 if variant == "low_capital_single_leg" else 1.0
            quote_size = round(float(min_size or 0.0) * size_multiplier, 8) if min_size is not None else None
            score = compute_liquidity_reward_score(
                midpoint=midpoint,
                order_price=quote_price,
                order_size=quote_size,
                side="YES",
                max_incentive_spread=max_spread,
                min_incentive_size=min_size,
                complement_order_price=no_quote,
                complement_order_size=min_size if no_quote is not None else None,
            )
            risk = evaluate_weather_lp_basket(
                legs=[{"entry_price": quote_price, "market_slug": market.get("market_slug")}],
                city_volatility_bucket=str(regime.get("volatility_bucket") or "insufficient_data"),
                reward_estimate=score.get("normalized_score_proxy"),
            )
            blockers: List[str] = []
            if market.get("reward_program_type") != "liquidity_reward":
                blockers.append("no_liquidity_reward_metadata")
            if not score.get("qualifies_for_reward"):
                blockers.extend(score.get("blockers") or ["quote_score_zero"])
            if risk.get("decision") == "reject":
                blockers.extend(risk.get("blockers") or ["basket_risk_rejected"])
            if risk.get("total_entry_cost", 0) >= 0.98:
                blockers.append("expensive_basket_near_full_payout")
            elif risk.get("total_entry_cost", 0) >= 0.95:
                blockers.append("expensive_basket_watch_only")
            if not regime or regime.get("volatility_bucket") == "insufficient_data":
                blockers.append("city_profile_insufficient")
            risk_score = round(max(0.0, float(risk.get("total_entry_cost") or 0.0)) + (0.5 if "expensive" in ",".join(blockers) else 0.0), 8)
            reward_to_risk = None
            if risk_score > 0:
                reward_to_risk = round(float(score.get("normalized_score_proxy") or 0.0) / risk_score, 8)
            row = {
                "schema_version": f"{SCHEMA_VERSION}.candidate",
                "market_slug": market.get("market_slug"),
                "token_id": market.get("token_id") or market.get("yes_token_id"),
                "city": market.get("city"),
                "station_code": market.get("station_code"),
                "strategy_variant": variant,
                "quote_prices": {"YES": quote_price, "NO": no_quote},
                "quote_sizes": {"YES": quote_size, "NO": min_size if no_quote is not None else None},
                "side": "YES",
                "reward_points_proxy": score.get("normalized_score_proxy"),
                "qualifies_for_reward": bool(score.get("qualifies_for_reward")),
                "basket_total_cost": risk.get("total_entry_cost"),
                "expected_time_on_book": "5m_cycle",
                "city_regime": regime.get("volatility_bucket") or "insufficient_data",
                "cancellation_policy": "cancel_at_hour_boundary" if "hour_boundary" in variant else "hold_full_window",
                "risk_score": risk_score,
                "reward_to_risk_proxy": reward_to_risk,
                "expensive_basket_blocker": risk.get("expensive_basket_blocker"),
                "blockers": sorted(set(blockers)),
                "decision": "selected_quote" if not blockers else "reject",
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
            rows.append(row)
            if not blockers:
                current = selected_by_market.get(str(market.get("market_slug") or ""))
                if current is None or float(row.get("reward_to_risk_proxy") or -1) > float(current.get("reward_to_risk_proxy") or -1):
                    selected_by_market[str(market.get("market_slug") or "")] = row
    selected = list(selected_by_market.values())
    blocker_counts = Counter(reason for row in rows for reason in (row.get("blockers") or []))
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "market_count": len(markets),
        "variant_count": len(rows),
        "reward_qualified_variant_count": len([row for row in rows if row.get("qualifies_for_reward")]),
        "selected_quote_count": len(selected),
        "rejected_expensive_basket_count": len([row for row in rows if "expensive_basket_near_full_payout" in (row.get("blockers") or [])]),
        "pareto_frontier_count": len(selected),
        "by_city": [
            {"city": key, "selected_quote_count": value}
            for key, value in sorted(Counter(str(row.get("city") or "missing") for row in selected).items())
        ],
        "by_strategy_variant": [
            {"strategy_variant": key, "selected_quote_count": value}
            for key, value in sorted(Counter(str(row.get("strategy_variant") or "missing") for row in selected).items())
        ],
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(blocker_counts.items())],
        "candidates": selected,
        "all_variants": rows,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


__all__ = ["SCHEMA_VERSION", "build_weather_lp_quote_optimizer", "load_jsonl", "write_json", "write_jsonl"]
