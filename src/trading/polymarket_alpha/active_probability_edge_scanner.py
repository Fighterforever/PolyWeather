from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import price_bucket, spread_bucket, time_to_close_bucket, write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_active_probability_edge.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _focused_categories(focus_report: Dict[str, Any]) -> set[str]:
    rows = focus_report.get("top_focus_categories") if isinstance(focus_report.get("top_focus_categories"), list) else []
    return {str(row.get("category")) for row in rows if isinstance(row, dict) and row.get("recommendation") == "focus_forward_paper"}


def _predict(row: Dict[str, Any], model_report: Dict[str, Any], price: float) -> Dict[str, Any]:
    model = model_report.get("model") if isinstance(model_report.get("model"), dict) else {}
    keys = [
        f"{row.get('category')}|{price_bucket(price)}",
        f"*|{price_bucket(price)}",
        f"{row.get('category')}|*",
    ]
    for key in keys:
        value = model.get(key)
        if isinstance(value, dict):
            return {
                "model_key": key,
                "p": float(value.get("p") or price),
                "p_lcb": float(value.get("p_lcb") if value.get("p_lcb") is not None else value.get("p") or price),
                "p_ucb": float(value.get("p_ucb") if value.get("p_ucb") is not None else value.get("p") or price),
                "sample_count": int(value.get("sample_count") or 0),
            }
    return {"model_key": "market_price_baseline", "p": price, "p_lcb": price, "p_ucb": price, "sample_count": 0}


def _books(row: Dict[str, Any]) -> List[tuple[str, Dict[str, Any], str]]:
    books = row.get("orderbooks") if isinstance(row.get("orderbooks"), dict) else {}
    token_to_outcome = {str(token): outcome for outcome, token in (row.get("token_id_by_outcome") or {}).items()}
    return [(token_id, book, token_to_outcome.get(str(token_id), "")) for token_id, book in books.items() if isinstance(book, dict)]


def scan_active_probability_edges(
    *,
    active_markets: Iterable[Dict[str, Any]],
    model_report: Dict[str, Any],
    focus_report: Dict[str, Any],
    min_edge: float = 0.02,
    min_depth: float = 10.0,
    max_spread: float = 0.15,
    cost: float = 0.01,
) -> Dict[str, Any]:
    focus = _focused_categories(focus_report)
    candidates: List[Dict[str, Any]] = []
    watch_rows: List[Dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    for market in active_markets:
        if not isinstance(market, dict):
            continue
        if not market.get("active"):
            blockers["not_active"] += 1
            continue
        category = str(market.get("category") or "uncategorized")
        if category not in focus:
            blockers["category_not_focus_forward_paper"] += 1
            continue
        for token_id, book, outcome_label in _books(market):
            best_ask = _safe_float(book.get("best_ask"))
            best_bid = _safe_float(book.get("best_bid"))
            spread = _safe_float(book.get("spread"))
            depth = _safe_float(book.get("ask_depth_usdc_3c"))
            if best_ask is None or best_bid is None:
                blockers["missing_bid_ask"] += 1
                continue
            if best_ask < 0.005:
                blockers["dust"] += 1
                continue
            if depth is None or depth < float(min_depth):
                blockers["depth_insufficient"] += 1
                continue
            if spread is None or spread > float(max_spread):
                blockers["spread_too_wide"] += 1
                continue
            price = round((best_bid + best_ask) / 2.0, 8)
            pred = _predict({**market, "token_id": token_id}, model_report, price)
            p_lcb = float(pred["p_lcb"])
            p_ucb = float(pred["p_ucb"])
            yes_ev = p_lcb - best_ask - float(cost)
            no_ask = 1.0 - best_bid
            no_ev = (1.0 - p_ucb) - no_ask - float(cost)
            side = "YES" if yes_ev >= no_ev else "NO"
            ev_safe = yes_ev if side == "YES" else no_ev
            watch = {
                "market_slug": market.get("market_slug"),
                "token_id": token_id,
                "category": category,
                "side": side,
                "outcome_label": outcome_label,
                "p_model": round(float(pred["p"]), 8),
                "p_lcb": round(p_lcb, 8),
                "p_ucb": round(p_ucb, 8),
                "market_price": price,
                "best_ask": best_ask if side == "YES" else no_ask,
                "q_effective": best_ask if side == "YES" else no_ask,
                "cost": float(cost),
                "EV_safe": round(ev_safe, 8),
                "model_source": "empirical_bucket_calibration",
                "confidence": min(1.0, math.sqrt(float(pred.get("sample_count") or 0) / 100.0)),
                "orderbook_snapshot_id": None,
                "price_bucket": price_bucket(price),
                "spread_bucket": spread_bucket(spread),
                "time_to_close_bucket": time_to_close_bucket(None),
                "neutral_political_stats_only": category in {"politics", "elections", "world_elections", "trump"},
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
            watch_rows.append(watch)
            if ev_safe >= float(min_edge):
                candidates.append(watch)
            else:
                blockers["ev_below_min"] += 1
    candidates = sorted(candidates, key=lambda row: float(row.get("EV_safe") or -1e9), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "focus_categories": sorted(focus),
        "watch_row_count": len(watch_rows),
        "candidate_count": len(candidates),
        "paper_fill_count": len(candidates),
        "blocker_counts": [{"reason": key, "count": count} for key, count in sorted(blockers.items())],
        "candidates": candidates,
        "watch_rows": watch_rows,
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


__all__ = ["SCHEMA_VERSION", "load_json", "load_jsonl", "scan_active_probability_edges", "write_json", "write_jsonl"]
