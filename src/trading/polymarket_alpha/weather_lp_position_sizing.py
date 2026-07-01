from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_position_sizing.v1"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_weather_lp_position_sizing_report(
    *,
    quotes: Iterable[Dict[str, Any]],
    profitability_rows: Iterable[Dict[str, Any]] = (),
    per_market_risk_cap_dollars: float = 10.0,
    per_city_exposure_cap_dollars: float = 25.0,
    total_weather_lp_exposure_cap_dollars: float = 100.0,
    tiny_live_per_market_cap_dollars: float = 2.0,
) -> Dict[str, Any]:
    profit_by_quote = {
        str(row.get("quote_id") or ""): row
        for row in profitability_rows
        if isinstance(row, dict) and row.get("quote_id")
    }
    rows: List[Dict[str, Any]] = []
    city_exposure: Dict[str, float] = defaultdict(float)
    total_exposure = 0.0
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        quote_id = str(quote.get("quote_id") or "")
        quote_price = _safe_float(quote.get("quote_price")) or 0.0
        quote_size = _safe_float(quote.get("quote_size") or quote.get("size")) or 0.0
        min_size = _safe_float(quote.get("min_incentive_size")) or 0.0
        basket_cost = _safe_float(quote.get("basket_total_cost") or quote.get("basket_cost"))
        capital = quote_price * quote_size
        max_size_by_market = per_market_risk_cap_dollars / quote_price if quote_price > 0 else 0.0
        recommended = min(quote_size, max_size_by_market)
        reasons: List[str] = []
        rejected = False
        if basket_cost is not None and basket_cost >= 0.98:
            rejected = True
            reasons.append("expensive_basket_near_full_payout")
            recommended = 0.0
        if min_size and quote_price * min_size > per_market_risk_cap_dollars:
            rejected = True
            reasons.append("min_incentive_size_exceeds_per_market_risk_cap")
            recommended = 0.0
        city = str(quote.get("city") or "missing")
        if city_exposure[city] + (recommended * quote_price) > per_city_exposure_cap_dollars:
            reasons.append("city_exposure_cap_would_bind")
            recommended = max(0.0, (per_city_exposure_cap_dollars - city_exposure[city]) / quote_price) if quote_price > 0 else 0.0
        if total_exposure + (recommended * quote_price) > total_weather_lp_exposure_cap_dollars:
            reasons.append("total_weather_lp_exposure_cap_would_bind")
            recommended = max(0.0, (total_weather_lp_exposure_cap_dollars - total_exposure) / quote_price) if quote_price > 0 else 0.0
        if recommended > 0:
            city_exposure[city] += recommended * quote_price
            total_exposure += recommended * quote_price
        profit = profit_by_quote.get(quote_id, {})
        reward_score = _safe_float(quote.get("reward_score") or quote.get("reward_points_proxy") or profit.get("cumulative_reward_points_proxy")) or 0.0
        risk_multiplier = 1.0
        if str(quote.get("city_regime") or "").lower() == "volatile":
            risk_multiplier = 1.5
        max_tiny_live_size = 0.0
        if quote_price > 0 and not rejected:
            max_tiny_live_size = min(recommended, tiny_live_per_market_cap_dollars / quote_price)
        rows.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "quote_id": quote_id,
                "market_slug": quote.get("market_slug"),
                "city": city,
                "station_code": quote.get("station_code"),
                "strategy_variant": quote.get("strategy_variant"),
                "quote_size": quote_size,
                "quote_price": quote_price,
                "max_loss_if_filled": round(capital, 8),
                "capital_locked_proxy": round(capital, 8),
                "basket_total_cost": basket_cost,
                "city_risk_multiplier": risk_multiplier,
                "reward_score_per_dollar_at_risk": round(reward_score / capital, 8) if capital > 0 else None,
                "recommended_paper_size": round(recommended, 8),
                "max_tiny_live_size_if_ever_allowed": round(max_tiny_live_size, 8),
                "rejected": rejected,
                "reason": ",".join(reasons) if reasons else "within_paper_sizing_caps",
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "quote_count": len(rows),
        "recommended_quote_count": len([row for row in rows if float(row.get("recommended_paper_size") or 0.0) > 0]),
        "rejected_quote_count": len([row for row in rows if row.get("rejected")]),
        "total_capital_locked_proxy": round(sum(float(row.get("capital_locked_proxy") or 0.0) for row in rows), 8),
        "recommended_total_capital_at_risk": round(total_exposure, 8),
        "per_market_risk_cap_dollars": per_market_risk_cap_dollars,
        "per_city_exposure_cap_dollars": per_city_exposure_cap_dollars,
        "total_weather_lp_exposure_cap_dollars": total_weather_lp_exposure_cap_dollars,
        "quote_size_limit_ready": True,
        "max_total_capital_at_risk_ready": True,
        "city_exposure_cap_ready": True,
        "expensive_basket_filter_ready": True,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
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


__all__ = ["SCHEMA_VERSION", "build_weather_lp_position_sizing_report", "load_jsonl", "write_json"]
