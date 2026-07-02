from __future__ import annotations

from typing import Any, Dict, Optional

from src.trading.polymarket_orderbook_archive import estimate_taker_effective_price
from src.trading.weather_paper_journal import _safe_float


EXECUTION_SIM_SCHEMA_VERSION = "polyweather_weather_execution_sim.v1"


def simulate_taker_buy(
    *,
    candidate: Dict[str, Any],
    orderbook_snapshot: Dict[str, Any],
    size: float = 1.0,
) -> Dict[str, Any]:
    order_book = {
        "ask_ladder": orderbook_snapshot.get("ask_ladder") or [],
        "bid_ladder": orderbook_snapshot.get("bid_ladder") or [],
    }
    estimate = estimate_taker_effective_price(order_book, side="buy", size=size)
    avg_price = _safe_float(estimate.get("avg_price"))
    return {
        "schema_version": EXECUTION_SIM_SCHEMA_VERSION,
        "paper_only": True,
        "source": "weather_execution_sim",
        "execution_style": "taker_buy",
        "available_at": (
            candidate.get("available_at")
            or candidate.get("generated_at")
            or candidate.get("recorded_at")
            or orderbook_snapshot.get("recorded_at")
        ),
        "candidate_id": candidate.get("row_id") or candidate.get("id"),
        "queue_name": candidate.get("queue_name"),
        "strategy_id": candidate.get("strategy_id"),
        "city": candidate.get("city"),
        "bucket_type": candidate.get("bucket_type"),
        "market_slug": candidate.get("market_slug"),
        "token_id": candidate.get("token_id"),
        "side": candidate.get("side"),
        "p_model": _safe_float(candidate.get("p_model") or candidate.get("model_probability")),
        "p_lcb": _safe_float(candidate.get("p_lcb")),
        "model_probability": _safe_float(candidate.get("model_probability") or candidate.get("p_model")),
        "q_effective": _safe_float(candidate.get("q_effective")),
        "ev_safe": _safe_float(candidate.get("ev_safe")),
        "requested_size": estimate.get("requested_size"),
        "filled_size": estimate.get("filled_size"),
        "unfilled_size": estimate.get("unfilled_size"),
        "fully_filled": estimate.get("fully_filled"),
        "entry_price": avg_price,
        "notional_usdc": estimate.get("notional_usdc"),
        "levels_consumed": estimate.get("levels_consumed"),
        "missed_fill": not bool(estimate.get("fully_filled")),
        "orderbook_snapshot_id": orderbook_snapshot.get("snapshot_id"),
        "orderbook_recorded_at": orderbook_snapshot.get("recorded_at"),
    }


def binary_fill_pnl(
    fill: Dict[str, Any],
    *,
    payout: Optional[float],
) -> Dict[str, Any]:
    entry_price = _safe_float(fill.get("entry_price"))
    filled_size = _safe_float(fill.get("filled_size")) or 0.0
    if entry_price is None or payout is None or filled_size <= 0:
        pnl_usdc = None
        pnl_cents = None
        roi_pct = None
    else:
        pnl_usdc = (float(payout) - entry_price) * filled_size
        pnl_cents = pnl_usdc * 100.0
        roi_pct = ((float(payout) / entry_price) - 1.0) * 100.0 if entry_price else None
    return {
        "payout": payout,
        "pnl_usdc": round(pnl_usdc, 8) if pnl_usdc is not None else None,
        "pnl_cents": round(pnl_cents, 6) if pnl_cents is not None else None,
        "roi_pct": round(roi_pct, 6) if roi_pct is not None else None,
    }
