from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import load_jsonl, write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_probability_edge_model.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _log_loss(p: float, y: float) -> float:
    p = min(1 - 1e-6, max(1e-6, float(p)))
    return -(float(y) * math.log(p) + (1 - float(y)) * math.log(1 - p))


def _event_split(rows: List[Dict[str, Any]], train_fraction: float = 0.7) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_category_event: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        category = str(row.get("category") or "uncategorized")
        event = str(row.get("event_slug") or row.get("market_slug") or row.get("token_id"))
        by_category_event[category][event].append(row)
    train: List[Dict[str, Any]] = []
    validate: List[Dict[str, Any]] = []
    for by_event in by_category_event.values():
        events = sorted(
            by_event,
            key=lambda event: min(str(row.get("timestamp") or "") for row in by_event[event]),
        )
        if len(events) <= 1:
            train.extend(row for event in events for row in by_event[event])
            continue
        cut = int(len(events) * float(train_fraction))
        cut = min(len(events) - 1, max(1, cut))
        train_events = set(events[:cut])
        train.extend(row for event in train_events for row in by_event[event])
        validate.extend(row for event in events[cut:] for row in by_event[event])
    return train, validate


def _fit_bins(train: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in train:
        groups[f"{row.get('category')}|{row.get('price_bucket')}"].append(row)
        groups[f"*|{row.get('price_bucket')}"].append(row)
        groups[f"{row.get('category')}|*"].append(row)
    model: Dict[str, Dict[str, Any]] = {}
    for key, rows in groups.items():
        payouts = [_safe_float(row.get("resolved_payout")) for row in rows]
        payouts = [value for value in payouts if value is not None]
        if not payouts:
            continue
        mean = sum(payouts) / len(payouts)
        se = math.sqrt(max(1e-9, mean * (1 - mean)) / max(1, len(payouts)))
        model[key] = {
            "p": mean,
            "sample_count": len(payouts),
            "p_lcb": max(0.0, mean - 1.96 * se),
            "p_ucb": min(1.0, mean + 1.96 * se),
        }
    return model


def _predict(row: Dict[str, Any], model: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    keys = [
        f"{row.get('category')}|{row.get('price_bucket')}",
        f"*|{row.get('price_bucket')}",
        f"{row.get('category')}|*",
    ]
    for key in keys:
        if key in model:
            return {"model_key": key, **model[key]}
    price = _safe_float(row.get("price_mid")) or 0.5
    return {"model_key": "market_price_baseline", "p": price, "p_lcb": price, "p_ucb": price, "sample_count": 0}


def _metrics(rows: List[Dict[str, Any]], model: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    pairs = []
    for row in rows:
        y = _safe_float(row.get("resolved_payout"))
        m = _safe_float(row.get("price_mid"))
        if y is None or m is None:
            continue
        p = _predict(row, model)["p"]
        pairs.append((m, p, y, row))
    if not pairs:
        return {
            "sample_count": 0,
            "brier_market": None,
            "brier_model": None,
            "brier_improvement": None,
            "log_loss_market": None,
            "log_loss_model": None,
            "log_loss_improvement": None,
        }
    brier_market = sum((m - y) ** 2 for m, _, y, _ in pairs) / len(pairs)
    brier_model = sum((p - y) ** 2 for _, p, y, _ in pairs) / len(pairs)
    ll_market = sum(_log_loss(m, y) for m, _, y, _ in pairs) / len(pairs)
    ll_model = sum(_log_loss(p, y) for _, p, y, _ in pairs) / len(pairs)
    return {
        "sample_count": len(pairs),
        "brier_market": round(brier_market, 8),
        "brier_model": round(brier_model, 8),
        "brier_improvement": round(brier_market - brier_model, 8),
        "log_loss_market": round(ll_market, 8),
        "log_loss_model": round(ll_model, 8),
        "log_loss_improvement": round(ll_market - ll_model, 8),
    }


def _candidate_for_row(row: Dict[str, Any], pred: Dict[str, Any], *, min_edge: float, cost: float) -> Optional[Dict[str, Any]]:
    price = _safe_float(row.get("price_mid"))
    if price is None:
        return None
    best_ask = _safe_float(row.get("best_ask")) or price
    p_lcb = float(pred.get("p_lcb"))
    p_ucb = float(pred.get("p_ucb"))
    yes_ev = p_lcb - best_ask - float(cost)
    no_ask = 1.0 - (_safe_float(row.get("best_bid")) if row.get("best_bid") is not None else price)
    no_ev = (1.0 - p_ucb) - no_ask - float(cost)
    if yes_ev >= no_ev:
        side = "YES"
        ev_safe = yes_ev
        ask = best_ask
    else:
        side = "NO"
        ev_safe = no_ev
        ask = no_ask
    if ev_safe < float(min_edge):
        return None
    payout = _safe_float(row.get("resolved_payout"))
    pnl = None
    if payout is not None:
        side_payout = payout if side == "YES" else 1.0 - payout
        pnl = round(100.0 * (side_payout - ask), 6)
    return {
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "category": row.get("category"),
        "side": side,
        "p_model": round(float(pred.get("p")), 8),
        "p_lcb": round(p_lcb, 8),
        "p_ucb": round(p_ucb, 8),
        "market_price": price,
        "best_ask": ask,
        "q_effective": ask,
        "cost": float(cost),
        "EV_safe": round(ev_safe, 8),
        "model_source": "empirical_bucket_calibration",
        "confidence": min(1.0, math.sqrt(float(pred.get("sample_count") or 0) / 100.0)),
        "orderbook_snapshot_id": None,
        "resolved_pnl_proxy": pnl,
        "resolved": payout is not None,
        "proxy_only": not bool(row.get("executable_depth_available")),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def build_probability_edge_model_report(
    rows: Iterable[Dict[str, Any]],
    *,
    min_edge: float = 0.02,
    cost: float = 0.01,
    train_fraction: float = 0.7,
) -> Dict[str, Any]:
    resolved = [
        row for row in rows
        if isinstance(row, dict) and row.get("resolved") and _safe_float(row.get("price_mid")) is not None and _safe_float(row.get("resolved_payout")) is not None
    ]
    events_by_category: Dict[str, set[str]] = defaultdict(set)
    resolved_rows_by_category: Counter[str] = Counter()
    for row in resolved:
        category = str(row.get("category") or "uncategorized")
        events_by_category[category].add(str(row.get("event_slug") or row.get("market_slug") or row.get("token_id")))
        resolved_rows_by_category[category] += 1
    oos_blockers = [
        {
            "category": category,
            "resolved_row_count": resolved_rows_by_category[category],
            "resolved_event_count": len(events),
            "reason": "single_resolved_event_family_no_leakage_safe_oos_split",
        }
        for category, events in sorted(events_by_category.items())
        if len(events) < 2
    ]
    train, validate = _event_split(resolved, train_fraction=train_fraction)
    model = _fit_bins(train)
    metrics = _metrics(validate, model)
    by_category: List[Dict[str, Any]] = []
    for category in sorted({str(row.get("category") or "uncategorized") for row in validate}):
        members = [row for row in validate if str(row.get("category") or "uncategorized") == category]
        cat_metrics = _metrics(members, model)
        cat_candidates = []
        for row in members:
            pred = _predict(row, model)
            candidate = _candidate_for_row(row, pred, min_edge=min_edge, cost=cost)
            if candidate:
                cat_candidates.append(candidate)
        by_category.append(
            {
                "category": category,
                **cat_metrics,
                "candidate_count": len(cat_candidates),
                "resolved_candidate_count": len([row for row in cat_candidates if row.get("resolved")]),
                "resolved_candidate_pnl_proxy": round(sum(float(row.get("resolved_pnl_proxy") or 0.0) for row in cat_candidates), 6) if cat_candidates else None,
                "positive_ev_candidate_rate": round(len(cat_candidates) / max(1, len(members)), 6),
            }
        )
    candidates: List[Dict[str, Any]] = []
    for row in validate:
        candidate = _candidate_for_row(row, _predict(row, model), min_edge=min_edge, cost=cost)
        if candidate:
            candidates.append(candidate)
    candidates = sorted(candidates, key=lambda row: float(row.get("EV_safe") or -1e9), reverse=True)
    positive_categories = [
        row for row in by_category
        if (row.get("brier_improvement") or 0) > 0 and (row.get("log_loss_improvement") or 0) > 0
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "model_sources": [
            "market_price_baseline",
            "empirical_bucket_calibration",
            "pure_python_bin_calibration",
        ],
        "sklearn_available": False,
        "input_resolved_row_count": len(resolved),
        "train_row_count": len(train),
        "validate_row_count": len(validate),
        "bucket_model_group_count": len(model),
        "overall": metrics,
        "by_category": sorted(by_category, key=lambda row: float(row.get("brier_improvement") or -1e9), reverse=True),
        "top_oos_brier_improvement_categories": sorted(by_category, key=lambda row: float(row.get("brier_improvement") or -1e9), reverse=True)[:5],
        "top_ev_candidate_rate_categories": sorted(by_category, key=lambda row: float(row.get("positive_ev_candidate_rate") or 0.0), reverse=True)[:5],
        "candidate_count": len(candidates),
        "resolved_candidate_count": len([row for row in candidates if row.get("resolved")]),
        "resolved_candidate_pnl_proxy": round(sum(float(row.get("resolved_pnl_proxy") or 0.0) for row in candidates), 6) if candidates else None,
        "positive_net_ev_after_cost": bool(candidates),
        "oos_split_blocker_counts": {
            "single_resolved_event_category_count": len(oos_blockers),
            "single_resolved_event_row_count": sum(int(row.get("resolved_row_count") or 0) for row in oos_blockers),
        },
        "oos_split_blockers": oos_blockers[:50],
        "hard_conclusion": (
            "probability_edge_candidate_categories_found"
            if positive_categories
            else "no_probability_edge_yet_insufficient_category_event_oos_split"
            if not validate and resolved
            else "no_probability_edge_yet"
        ),
        "model": model,
        "candidates": candidates,
    }


__all__ = ["SCHEMA_VERSION", "build_probability_edge_model_report", "load_jsonl", "write_json", "write_jsonl"]
