from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl
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
        if not row.get("reward_available"):
            blockers.append(str(row.get("gap_reason") or "reward_metadata_missing"))
        if not regime or regime.get("volatility_bucket") == "insufficient_data":
            blockers.append("city_profile_insufficient")
        best_ask = _safe_float(row.get("best_ask") or row.get("yes_best_ask"))
        if best_ask is None:
            blockers.append("missing_executable_ask")
        reward_score = _safe_float(row.get("reward_score"))
        if reward_score is None:
            blockers.append("missing_reward_score")

        variant = "low_price_single_leg" if best_ask is not None and best_ask <= cheap_leg_threshold else "high_reward_dual_leg"
        risk = evaluate_weather_lp_basket(
            legs=[{"entry_price": best_ask or 0.0, "market_slug": row.get("market_slug")}],
            city_volatility_bucket=str(regime.get("volatility_bucket") or "insufficient_data"),
            reward_estimate=reward_score,
            includes_smart_holder_bucket=bool((holder.get("smart_holder_score") or 0) > 0),
        )
        if risk.get("decision") == "reject" or risk.get("total_entry_cost", 0) >= max_basket_cost:
            blockers.append("basket_risk_rejected")
        decision = "paper_quote" if not blockers else "watch_only" if row.get("reward_available") else "reject"
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
            "quote_price": best_ask,
            "current_best_bid": row.get("best_bid"),
            "current_best_ask": best_ask,
            "spread": row.get("spread"),
            "reward_score": reward_score,
            "reward_estimate": reward_score,
            "city_regime": regime.get("volatility_bucket") or "insufficient_data",
            "basket_total_cost": risk.get("total_entry_cost"),
            "basket_width": risk.get("basket_width"),
            "smart_holder_score": holder.get("smart_holder_score"),
            "time_window": "unvalidated",
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
        "candidate_count": len(candidates),
        "watch_count": len(watch_rows),
        "reject_count": len(rejected),
        "expensive_basket_rejection_count": len([row for row in rejected + watch_rows if "basket_risk_rejected" in (row.get("blockers") or [])]),
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
