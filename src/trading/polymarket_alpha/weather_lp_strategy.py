from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
from src.trading.polymarket_alpha.liquidity_reward_score import compute_liquidity_reward_score
from src.trading.polymarket_alpha.weather_lp_basket_risk import evaluate_weather_lp_basket


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_strategy.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _regime_by_city(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("city") or "").lower(): row for row in rows if isinstance(row, dict)}


def _holder_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("token_id") or ""): row for row in rows if isinstance(row, dict)}


def _midpoint(row: Dict[str, Any]) -> Optional[float]:
    bid = _safe_float(row.get("best_bid") or row.get("yes_best_bid"))
    ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 8)


def _quote_price(row: Dict[str, Any], *, midpoint: Optional[float], tick: float = 0.001) -> Optional[float]:
    bid = _safe_float(row.get("best_bid") or row.get("yes_best_bid"))
    ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
    max_spread = _safe_float(row.get("max_incentive_spread"))
    if bid is None or ask is None or midpoint is None or ask <= bid:
        return None
    target = midpoint - min((max_spread or 0.01) / 2.0, max((ask - bid) / 4.0, tick))
    price = max(bid + tick, min(target, ask - tick))
    if price <= 0 or price >= 1:
        return None
    return round(price, 4)


def _no_quote_price(row: Dict[str, Any], *, midpoint: Optional[float], tick: float = 0.001) -> Optional[float]:
    bid = _safe_float(row.get("no_best_bid"))
    ask = _safe_float(row.get("no_best_ask"))
    if bid is None or ask is None or midpoint is None or ask <= bid:
        return None
    no_midpoint = 1.0 - midpoint
    max_spread = _safe_float(row.get("max_incentive_spread"))
    target = no_midpoint - min((max_spread or 0.01) / 2.0, max((ask - bid) / 4.0, tick))
    price = max(bid + tick, min(target, ask - tick))
    if price <= 0 or price >= 1:
        return None
    return round(price, 4)


def build_weather_lp_strategy(
    *,
    reward_markets: Iterable[Dict[str, Any]],
    city_regimes: Iterable[Dict[str, Any]] = (),
    smart_holder_signals: Iterable[Dict[str, Any]] = (),
    max_basket_cost: float = 0.95,
    cheap_leg_threshold: float = 0.05,
) -> Dict[str, Any]:
    markets = [row for row in reward_markets if isinstance(row, dict)]
    regimes = _regime_by_city(city_regimes)
    holders = _holder_by_token(smart_holder_signals)
    candidates: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for row in markets:
        city = str(row.get("city") or "").lower()
        regime = regimes.get(city) or {}
        holder = holders.get(str(row.get("token_id") or row.get("yes_token_id") or "")) or {}
        blockers: List[str] = []
        if row.get("reward_program_type") != "liquidity_reward" or not row.get("reward_metadata_available"):
            blockers.append(str(row.get("gap_reason") or "reward_metadata_missing"))
        if not regime or regime.get("volatility_bucket") == "insufficient_data":
            blockers.append("city_profile_insufficient")
        best_ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
        best_bid = _safe_float(row.get("best_bid") or row.get("yes_best_bid"))
        if best_ask is None or best_bid is None:
            blockers.append("missing_orderbook")
        min_size = _safe_float(row.get("min_incentive_size"))
        max_spread = _safe_float(row.get("max_incentive_spread"))
        if min_size is None or max_spread is None:
            blockers.append("missing_liquidity_reward_parameters")

        midpoint = _midpoint(row)
        quote_price = _quote_price(row, midpoint=midpoint)
        no_quote = _no_quote_price(row, midpoint=midpoint)
        variant = "single_sided_low_risk_quote" if midpoint is not None and 0.10 <= midpoint <= 0.90 else "dual_sided_reward_quote"
        if best_ask is not None and best_ask <= cheap_leg_threshold:
            variant = "low_price_single_leg"
        reward_score_payload = compute_liquidity_reward_score(
            midpoint=midpoint,
            order_price=quote_price,
            order_size=min_size,
            side="YES",
            max_incentive_spread=max_spread,
            min_incentive_size=min_size,
            complement_order_price=no_quote,
            complement_order_size=min_size if no_quote is not None else None,
        )
        if not reward_score_payload.get("qualifies_for_reward"):
            blockers.extend(str(item) for item in (reward_score_payload.get("blockers") or ["does_not_qualify_for_reward_score"]))
        risk = evaluate_weather_lp_basket(
            legs=[{"entry_price": quote_price if quote_price is not None else (best_ask or 0.0), "market_slug": row.get("market_slug")}],
            city_volatility_bucket=str(regime.get("volatility_bucket") or "insufficient_data"),
            reward_estimate=reward_score_payload.get("normalized_score_proxy"),
            includes_smart_holder_bucket=bool((holder.get("smart_holder_score") or 0) > 0),
        )
        if risk.get("decision") == "reject" or risk.get("total_entry_cost", 0) >= max_basket_cost:
            blockers.append("basket_risk_rejected")
        decision = "paper_quote" if not blockers else "watch_only" if row.get("reward_metadata_available") else "reject"
        payload = {
            "schema_version": f"{SCHEMA_VERSION}.candidate",
            "strategy_id": f"weather_lp_reward_city_specific:{variant}",
            "strategy_variant": variant,
            "city": city or row.get("city"),
            "station_code": row.get("station_code"),
            "target_date": row.get("target_date"),
            "market_slug": row.get("market_slug"),
            "token_id": row.get("token_id") or row.get("yes_token_id"),
            "side": "YES",
            "bucket_type": row.get("bucket_type"),
            "threshold": row.get("threshold"),
            "quote_price": quote_price,
            "quote_size": min_size,
            "midpoint": midpoint,
            "spread_from_midpoint": reward_score_payload.get("spread_from_midpoint"),
            "max_incentive_spread": max_spread,
            "min_incentive_size": min_size,
            "current_best_bid": row.get("best_bid"),
            "current_best_ask": best_ask,
            "spread": row.get("spread"),
            "reward_program_type": row.get("reward_program_type"),
            "reward_score": reward_score_payload.get("normalized_score_proxy"),
            "reward_score_at_entry": reward_score_payload,
            "reward_estimate": reward_score_payload.get("normalized_score_proxy"),
            "reward_allocation": row.get("reward_allocation"),
            "city_regime": regime.get("volatility_bucket") or "insufficient_data",
            "basket_total_cost": risk.get("total_entry_cost"),
            "basket_width": risk.get("basket_width"),
            "smart_holder_score": holder.get("smart_holder_score"),
            "time_window": "unvalidated",
            "minute_of_hour": row.get("minute_of_hour"),
            "decision": decision,
            "blockers": blockers,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        if decision == "paper_quote":
            candidates.append(payload)
        elif decision == "watch_only":
            watch_rows.append(payload)
        else:
            rejected.append(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "input_market_count": len(markets),
        "scanned_weather_markets": len(markets),
        "reward_metadata_available_count": len([row for row in markets if row.get("reward_metadata_available")]),
        "reward_qualified_quote_count": len(candidates) + len([row for row in watch_rows if row.get("reward_score_at_entry", {}).get("qualifies_for_reward")]),
        "candidate_count": len(candidates),
        "paper_quote_candidate_count": len(candidates),
        "watch_count": len(watch_rows),
        "watch_only_count": len(watch_rows),
        "reject_count": len(rejected),
        "expensive_basket_rejection_count": len([row for row in rejected + watch_rows if "basket_risk_rejected" in (row.get("blockers") or [])]),
        "no_candidate_reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                Counter(reason for row in rejected + watch_rows for reason in (row.get("blockers") or [])).items()
            )
        ],
        "by_city": [
            {"city": city, "candidate_count": count}
            for city, count in sorted(Counter(str(row.get("city") or "missing") for row in candidates).items())
        ],
        "by_minute_of_hour": [
            {"minute_of_hour": minute, "candidate_count": count}
            for minute, count in sorted(Counter(str(row.get("minute_of_hour") or "missing") for row in candidates).items())
        ],
        "candidates": candidates,
        "watch_rows": watch_rows,
        "rejected": rejected,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


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


__all__ = ["SCHEMA_VERSION", "build_weather_lp_strategy", "load_json", "load_jsonl", "write_json", "write_jsonl"]
