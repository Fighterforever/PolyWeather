from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_paper_journal.v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_id(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def build_weather_lp_paper_cycle(
    *,
    candidates: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _now()
    quotes: List[Dict[str, Any]] = []
    fills: List[Dict[str, Any]] = []
    markouts: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("decision") != "paper_quote":
            continue
        quote_id = _stable_id({"market_slug": candidate.get("market_slug"), "token_id": candidate.get("token_id"), "generated_at": generated_at})
        reward = float(candidate.get("reward_estimate") or 0.0)
        quote = {
            "schema_version": f"{SCHEMA_VERSION}.quote",
            "quote_id": quote_id,
            "strategy_id": candidate.get("strategy_id"),
            "market_slug": candidate.get("market_slug"),
            "token_id": candidate.get("token_id"),
            "side": candidate.get("side"),
            "quote_price": candidate.get("quote_price"),
            "quote_start_time": generated_at,
            "quote_end_time": None,
            "minute_of_hour": datetime.fromisoformat(generated_at.replace("Z", "+00:00")).minute,
            "intended_reward_window": candidate.get("time_window"),
            "cancel_at_hour_boundary": True,
            "city": candidate.get("city"),
            "station_code": candidate.get("station_code"),
            "reward_score": candidate.get("reward_score"),
            "basket_cost": candidate.get("basket_total_cost"),
            "orderbook_snapshot_id": None,
            "reward_window_presence": bool(candidate.get("reward_score") is not None),
            "time_on_book_seconds": 0,
            "estimated_reward_points": reward,
            "estimated_reward_cents": reward,
            "quote_touched": False,
            "inferred_fill": False,
            "estimated_reward_cents_separate_from_markout": True,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        quotes.append(quote)
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "paper_quote_count": len(quotes),
        "inferred_fill_count": len(fills),
        "markout_count": len(markouts),
        "estimated_reward_points": round(sum(float(row.get("estimated_reward_points") or 0.0) for row in quotes), 8),
        "estimated_reward_cents": round(sum(float(row.get("estimated_reward_cents") or 0.0) for row in quotes), 8),
        "net_estimated_pnl_without_reward": 0.0 if quotes else None,
        "net_estimated_pnl_with_reward": round(sum(float(row.get("estimated_reward_cents") or 0.0) for row in quotes), 8) if quotes else None,
        "reward_is_guaranteed": False,
        "quotes": quotes,
        "fills": fills,
        "markouts": markouts,
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


__all__ = ["SCHEMA_VERSION", "build_weather_lp_paper_cycle", "load_jsonl", "write_json", "write_jsonl"]
