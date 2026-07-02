from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_smart_holder_signal.v1"


def build_weather_smart_holder_signal(
    *,
    markets: Iterable[Dict[str, Any]],
    holder_rows: Iterable[Dict[str, Any]] = (),
    known_wallets: Iterable[str] = (),
) -> Dict[str, Any]:
    holders = [row for row in holder_rows if isinstance(row, dict)]
    known = {str(wallet).lower() for wallet in known_wallets}
    by_token: Dict[str, List[Dict[str, Any]]] = {}
    for row in holders:
        by_token.setdefault(str(row.get("token_id") or ""), []).append(row)
    signals: List[Dict[str, Any]] = []
    for market in markets:
        token_id = str(market.get("token_id") or market.get("yes_token_id") or "")
        token_holders = by_token.get(token_id, [])
        known_present = any(str(row.get("wallet") or "").lower() in known for row in token_holders)
        concentration = sum(float(row.get("balance") or 0.0) for row in token_holders[:3]) if token_holders else None
        score = min(1.0, 0.2 + 0.2 * len(token_holders) + (0.4 if known_present else 0.0)) if token_holders else 0.0
        signals.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "market_slug": market.get("market_slug"),
                "token_id": token_id or None,
                "outcome_label": market.get("outcome_label") or market.get("bucket_label"),
                "top_holder_count": len(token_holders),
                "top_holder_concentration": concentration,
                "whale_holder_present": bool(token_holders and concentration and concentration > 0),
                "known_weather_wallet_present": bool(known_present),
                "smart_holder_score": round(score, 6),
                "confidence": "low" if token_holders else "unavailable",
                "data_source": "public_holder_rows" if token_holders else None,
                "gap_reason": None if token_holders else "holder_data_unavailable",
                "supporting_feature_only": True,
                "can_override_expensive_basket_blocker": False,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "signal_count": len(signals),
        "smart_holder_signal_count": len([row for row in signals if row.get("smart_holder_score", 0) > 0]),
        "gap_count": len([row for row in signals if row.get("gap_reason")]),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "signals": signals,
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


__all__ = ["SCHEMA_VERSION", "build_weather_smart_holder_signal", "load_jsonl", "write_json", "write_jsonl"]
