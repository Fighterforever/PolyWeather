from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = "polyweather_polymarket_alpha_maker_shadow.v1"
STRATEGY_ID = "polymarket_alpha_maker_shadow"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _books(row: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    orderbooks = row.get("orderbooks") if isinstance(row.get("orderbooks"), dict) else {}
    return [(str(token_id), book) for token_id, book in orderbooks.items() if isinstance(book, dict)]


def _depth(book: Dict[str, Any]) -> float:
    return float(_safe_float(book.get("ask_depth_usdc_3c")) or 0.0)


def build_maker_shadow_report(
    *,
    active_markets: Iterable[Dict[str, Any]],
    opportunity_scoreboard: Dict[str, Any],
    min_spread: float = 0.03,
    min_depth: float = 10.0,
    maker_margin: float = 0.01,
    max_categories: int = 5,
) -> Dict[str, Any]:
    top_categories = [
        str(row.get("category"))
        for row in (opportunity_scoreboard.get("top_categories") or [])[: max(1, int(max_categories))]
        if row.get("category")
    ]
    if not top_categories:
        top_categories = [str(value) for value in (opportunity_scoreboard.get("top_focus_categories") or [])[:max_categories]]
    top_set = set(top_categories)
    quotes: List[Dict[str, Any]] = []
    blocker_counts: Dict[str, int] = {}

    def block(reason: str) -> None:
        blocker_counts[reason] = blocker_counts.get(reason, 0) + 1

    for row in active_markets:
        if not isinstance(row, dict):
            continue
        if top_set and str(row.get("category")) not in top_set:
            block("category_not_top_focus")
            continue
        if not row.get("active"):
            block("not_active")
            continue
        market_books = _books(row)
        if not market_books:
            block("missing_orderbook")
            continue
        for token_id, book in market_books:
            best_bid = _safe_float(book.get("best_bid"))
            best_ask = _safe_float(book.get("best_ask"))
            spread = _safe_float(book.get("spread"))
            if best_ask is None or best_bid is None or spread is None:
                block("missing_bid_ask")
                continue
            if best_ask < 0.005:
                block("dust_price")
                continue
            if spread < float(min_spread):
                block("spread_below_min")
                continue
            if _depth(book) < float(min_depth):
                block("depth_below_min")
                continue
            fair_value = round((best_bid + best_ask) / 2.0, 6)
            quote_bid = round(fair_value - float(maker_margin), 6)
            quote_ask = round(fair_value + float(maker_margin), 6)
            if not (best_bid < quote_bid < best_ask and best_bid < quote_ask < best_ask):
                block("quote_not_inside_spread")
                continue
            edge_after_cost = min(quote_bid - best_bid, best_ask - quote_ask)
            if edge_after_cost <= 0:
                block("edge_after_cost_nonpositive")
                continue
            for quote_type, quote_price in (("maker_bid", quote_bid), ("maker_ask", quote_ask)):
                quotes.append(
                    {
                        "strategy_id": STRATEGY_ID,
                        "category": row.get("category"),
                        "market_slug": row.get("market_slug"),
                        "event_slug": row.get("event_slug"),
                        "token_id": token_id,
                        "quote_type": quote_type,
                        "fair_value": fair_value,
                        "quote_price": quote_price,
                        "current_best_bid": best_bid,
                        "current_best_ask": best_ask,
                        "spread": spread,
                        "edge_after_cost": round(edge_after_cost, 6),
                        "queue_proxy": "inside_spread_no_queue_position",
                        "touch_event": False,
                        "inferred_fill": False,
                        "fill_confidence": 0.0,
                        "paper_only": True,
                        "counts_for_live_gate": False,
                        "live_order_path": False,
                    }
                )
    fills: List[Dict[str, Any]] = []
    markouts: List[Dict[str, Any]] = []
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "top_categories": top_categories,
        "quote_count": len(quotes),
        "inferred_fill_count": len(fills),
        "markout_count": len(markouts),
        "mean_markout_without_rebate": None,
        "mean_markout_with_rebate": None,
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "quotes": quotes,
        "inferred_fills": fills,
        "markouts": markouts,
    }


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


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


__all__ = ["SCHEMA_VERSION", "STRATEGY_ID", "build_maker_shadow_report", "load_json", "load_jsonl", "write_json", "write_jsonl"]
