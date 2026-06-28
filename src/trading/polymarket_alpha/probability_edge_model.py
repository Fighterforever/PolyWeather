from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import load_jsonl, write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_probability_edge_model.v2"


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _log_loss(p: float, y: float) -> float:
    p = min(1 - 1e-6, max(1e-6, float(p)))
    return -(float(y) * math.log(p) + (1 - float(y)) * math.log(1 - p))


def _is_mid_training_row(row: Dict[str, Any]) -> bool:
    price = _safe_float(row.get("price_mid"))
    return price is not None and 0.10 <= price <= 0.90


def _is_extreme(row: Dict[str, Any]) -> bool:
    price = _safe_float(row.get("price_mid"))
    return price is None or price < 0.05 or price > 0.95


def _family_id(row: Dict[str, Any]) -> str:
    return str(row.get("event_family_id") or row.get("event_slug") or row.get("market_slug") or row.get("token_id"))


def _decision_time(row: Dict[str, Any]) -> str:
    return str(row.get("decision_time") or row.get("timestamp") or "")


def _family_split(rows: List[Dict[str, Any]], train_fraction: float = 0.7) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[_family_id(row)].append(row)
    families = sorted(
        by_family,
        key=lambda family: min(_decision_time(row) for row in by_family[family]),
    )
    if len(families) <= 1:
        return rows, [], {
            "status": "blocked",
            "reason": "need_at_least_two_event_families_for_leave_family_out",
            "family_count": len(families),
        }
    cut = int(len(families) * float(train_fraction))
    cut = min(len(families) - 1, max(1, cut))
    train_families = set(families[:cut])
    validate_families = set(families[cut:])
    train = [row for family in train_families for row in by_family[family]]
    validate = [row for family in validate_families for row in by_family[family]]
    return train, validate, {
        "status": "ok",
        "split_method": "time_ordered_leave_event_family_out",
        "family_count": len(families),
        "train_family_count": len(train_families),
        "validate_family_count": len(validate_families),
        "train_families": sorted(train_families)[:25],
        "validate_families": sorted(validate_families)[:25],
    }


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _fit_probability_group(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    payouts = [_safe_float(row.get("resolved_payout")) for row in rows]
    payouts = [value for value in payouts if value is not None]
    if not payouts:
        return None
    mean = sum(payouts) / len(payouts)
    se = math.sqrt(max(1e-9, mean * (1 - mean)) / max(1, len(payouts)))
    return {
        "p": mean,
        "sample_count": len(payouts),
        "p_lcb": max(0.0, mean - 1.96 * se),
        "p_ucb": min(1.0, mean + 1.96 * se),
    }


def _fit_bins(train: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    category_families: Dict[str, set[str]] = defaultdict(set)
    for row in train:
        category = str(row.get("category") or "uncategorized")
        category_families[category].add(_family_id(row))
    for row in train:
        price_bucket = str(row.get("price_bucket") or "missing")
        time_bucket = str(row.get("time_to_close_bucket") or "missing")
        spread_bucket = str(row.get("spread_bucket") or "missing")
        category = str(row.get("category") or "uncategorized")
        groups[f"global|{price_bucket}|{time_bucket}|{spread_bucket}"].append(row)
        groups[f"global|{price_bucket}|{time_bucket}|*"].append(row)
        groups[f"global|{price_bucket}|*|*"].append(row)
        groups["global|*|*|*"].append(row)
        if len(category_families[category]) >= 2:
            groups[f"category|{category}|{price_bucket}"].append(row)
    model: Dict[str, Dict[str, Any]] = {}
    for key, members in groups.items():
        fitted = _fit_probability_group(members)
        if fitted:
            model[key] = fitted
    return model


def _features(row: Dict[str, Any]) -> List[float]:
    price = _safe_float(row.get("price_mid")) or 0.5
    spread = _safe_float(row.get("spread")) or 0.05
    depth = _safe_float(row.get("depth")) or 0.0
    volume = _safe_float(row.get("volume")) or 0.0
    time_to_close = max(0.0, _safe_float(row.get("time_to_close_seconds")) or 0.0)
    momentum_5m = _safe_float(row.get("price_momentum_5m")) or 0.0
    momentum_1h = _safe_float(row.get("price_momentum_1h")) or 0.0
    category_text = str(row.get("category") or "uncategorized")
    category_hash = (sum((idx + 1) * ord(char) for idx, char in enumerate(category_text)) % 997) / 997.0
    return [
        1.0,
        price,
        math.log1p(max(0.0, spread)),
        math.log1p(max(0.0, depth)),
        math.log1p(max(0.0, volume)),
        math.log1p(time_to_close),
        momentum_5m,
        momentum_1h,
        category_hash,
    ]


def _sigmoid(value: float) -> float:
    if value < -35:
        return 1e-6
    if value > 35:
        return 1 - 1e-6
    return 1.0 / (1.0 + math.exp(-value))


def _fit_logistic(train: List[Dict[str, Any]], *, iterations: int = 250, learning_rate: float = 0.03) -> Dict[str, Any]:
    if len(train) < 20:
        return {"ready": False, "reason": "not_enough_rows_for_pure_python_logistic", "weights": []}
    weights = [0.0] * len(_features(train[0]))
    for _ in range(iterations):
        gradients = [0.0] * len(weights)
        for row in train:
            y = _safe_float(row.get("resolved_payout"))
            if y is None:
                continue
            x = _features(row)
            pred = _sigmoid(sum(w * value for w, value in zip(weights, x)))
            for idx, value in enumerate(x):
                gradients[idx] += (pred - y) * value
        scale = max(1, len(train))
        for idx in range(len(weights)):
            weights[idx] -= learning_rate * gradients[idx] / scale
            weights[idx] = max(-10.0, min(10.0, weights[idx]))
    return {"ready": True, "weights": weights}


def _predict_logistic(row: Dict[str, Any], logistic: Dict[str, Any]) -> Optional[float]:
    if not logistic.get("ready"):
        return None
    weights = logistic.get("weights") if isinstance(logistic.get("weights"), list) else []
    if not weights:
        return None
    return _sigmoid(sum(float(w) * value for w, value in zip(weights, _features(row))))


def _predict(row: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    bins = model.get("bins") if isinstance(model.get("bins"), dict) else {}
    price_bucket = str(row.get("price_bucket") or "missing")
    time_bucket = str(row.get("time_to_close_bucket") or "missing")
    spread_bucket = str(row.get("spread_bucket") or "missing")
    category = str(row.get("category") or "uncategorized")
    keys = [
        f"category|{category}|{price_bucket}",
        f"global|{price_bucket}|{time_bucket}|{spread_bucket}",
        f"global|{price_bucket}|{time_bucket}|*",
        f"global|{price_bucket}|*|*",
        "global|*|*|*",
    ]
    empirical: Optional[Dict[str, Any]] = None
    model_key = "market_price_baseline"
    for key in keys:
        value = bins.get(key)
        if isinstance(value, dict):
            empirical = value
            model_key = key
            break
    market_price = _safe_float(row.get("price_mid")) or 0.5
    logistic_p = _predict_logistic(row, model.get("logistic") if isinstance(model.get("logistic"), dict) else {})
    if empirical is None and logistic_p is None:
        return {"model_key": model_key, "p": market_price, "p_lcb": market_price, "p_ucb": market_price, "sample_count": 0}
    if empirical is None:
        sample_count = 0
        p = float(logistic_p)
        se = 0.08
    elif logistic_p is None:
        sample_count = int(empirical.get("sample_count") or 0)
        p = float(empirical.get("p"))
        se = math.sqrt(max(1e-9, p * (1 - p)) / max(1, sample_count))
    else:
        sample_count = int(empirical.get("sample_count") or 0)
        p = 0.70 * float(empirical.get("p")) + 0.30 * float(logistic_p)
        se = math.sqrt(max(1e-9, p * (1 - p)) / max(1, sample_count))
    return {
        "model_key": model_key,
        "p": max(0.0, min(1.0, p)),
        "p_lcb": max(0.0, min(1.0, p - 1.96 * se)),
        "p_ucb": max(0.0, min(1.0, p + 1.96 * se)),
        "sample_count": sample_count,
    }


def _metrics(rows: List[Dict[str, Any]], model: Dict[str, Any]) -> Dict[str, Any]:
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
    best_ask = _safe_float(row.get("best_ask"))
    best_bid = _safe_float(row.get("best_bid"))
    depth = _safe_float(row.get("depth"))
    if price is None or best_ask is None or best_bid is None or depth is None or depth <= 0:
        return None
    if _is_extreme(row):
        return None
    p_lcb = float(pred.get("p_lcb"))
    p_ucb = float(pred.get("p_ucb"))
    yes_ev = p_lcb - best_ask - float(cost)
    no_ask = 1.0 - best_bid
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
        "event_family_id": row.get("event_family_id"),
        "side": side,
        "p_model": round(float(pred.get("p")), 8),
        "p_lcb": round(p_lcb, 8),
        "p_ucb": round(p_ucb, 8),
        "market_price": price,
        "best_ask": ask,
        "q_effective": ask,
        "cost": float(cost),
        "EV_safe": round(ev_safe, 8),
        "model_source": "global_leave_family_out_probability_model",
        "confidence": min(1.0, math.sqrt(float(pred.get("sample_count") or 0) / 100.0)),
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id"),
        "resolved_pnl_proxy": pnl,
        "resolved": payout is not None,
        "proxy_only": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _prediction_row(row: Dict[str, Any], pred: Dict[str, Any]) -> Dict[str, Any]:
    payout = _safe_float(row.get("resolved_payout"))
    price = _safe_float(row.get("price_mid"))
    return {
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "category": row.get("category"),
        "event_family_id": row.get("event_family_id"),
        "decision_time": row.get("decision_time") or row.get("timestamp"),
        "price_mid": price,
        "resolved_payout": payout,
        "p_model": round(float(pred.get("p")), 8),
        "p_lcb": round(float(pred.get("p_lcb")), 8),
        "p_ucb": round(float(pred.get("p_ucb")), 8),
        "model_key": pred.get("model_key"),
        "brier_market": round((price - payout) ** 2, 8) if price is not None and payout is not None else None,
        "brier_model": round((float(pred.get("p")) - payout) ** 2, 8) if payout is not None else None,
        "log_loss_market": round(_log_loss(price, payout), 8) if price is not None and payout is not None else None,
        "log_loss_model": round(_log_loss(float(pred.get("p")), payout), 8) if payout is not None else None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def _group_metrics(rows: List[Dict[str, Any]], model: Dict[str, Any], field: str) -> List[Dict[str, Any]]:
    output = []
    for key in sorted({str(row.get(field) or "missing") for row in rows}):
        members = [row for row in rows if str(row.get(field) or "missing") == key]
        metrics = _metrics(members, model)
        output.append({field: key, **metrics})
    return sorted(output, key=lambda row: float(row.get("brier_improvement") or -1e9), reverse=True)


def build_probability_edge_model_report(
    rows: Iterable[Dict[str, Any]],
    *,
    min_edge: float = 0.02,
    cost: float = 0.01,
    train_fraction: float = 0.7,
) -> Dict[str, Any]:
    materialized = [row for row in rows if isinstance(row, dict)]
    resolved = [
        row
        for row in materialized
        if row.get("resolved")
        and _safe_float(row.get("price_mid")) is not None
        and _safe_float(row.get("resolved_payout")) is not None
    ]
    unique_resolved_event_family_count = len({_family_id(row) for row in resolved if _family_id(row)})
    mid_rows = [row for row in resolved if _is_mid_training_row(row)]
    train, validate, split_info = _family_split(mid_rows, train_fraction=train_fraction)
    bins = _fit_bins(train)
    logistic = _fit_logistic(train)
    model = {"bins": bins, "logistic": logistic}
    metrics = _metrics(validate, model)
    by_category = _group_metrics(validate, model, "category")
    by_price_bucket = _group_metrics(validate, model, "price_bucket")
    by_time_to_close = _group_metrics(validate, model, "time_to_close_bucket")
    predictions = [_prediction_row(row, _predict(row, model)) for row in validate]
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
    blocker_reason = None
    if split_info.get("status") != "ok":
        blocker_reason = split_info.get("reason")
    elif not validate:
        blocker_reason = "empty_validation_rows_after_mid_price_filter"
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "model_sources": [
            "market_price_baseline",
            "global_empirical_calibration",
            "global_logistic_pure_python",
            "category_shrinkage_when_family_count_sufficient",
        ],
        "sklearn_available": False,
        "input_row_count": len(materialized),
        "input_resolved_row_count": len(resolved),
        "unique_resolved_event_family_count": unique_resolved_event_family_count,
        "mid_price_training_row_count": len(mid_rows),
        "train_row_count": len(train),
        "validate_row_count": len(validate),
        "split": split_info,
        "bucket_model_group_count": len(bins),
        "logistic_model_ready": bool(logistic.get("ready")),
        "overall": metrics,
        "by_category": by_category,
        "by_price_bucket": by_price_bucket,
        "by_time_to_close": by_time_to_close,
        "top_oos_brier_improvement_categories": by_category[:5],
        "top_ev_candidate_rate_categories": sorted(
            [
                {
                    **row,
                    "candidate_count": len([cand for cand in candidates if cand.get("category") == row.get("category")]),
                    "positive_ev_candidate_rate": round(
                        len([cand for cand in candidates if cand.get("category") == row.get("category")])
                        / max(1, _safe_int(row.get("sample_count"))),
                        6,
                    ),
                }
                for row in by_category
            ],
            key=lambda row: float(row.get("positive_ev_candidate_rate") or 0.0),
            reverse=True,
        )[:5],
        "candidate_count": len(candidates),
        "resolved_candidate_count": len([row for row in candidates if row.get("resolved")]),
        "resolved_candidate_pnl_proxy": round(sum(float(row.get("resolved_pnl_proxy") or 0.0) for row in candidates), 6) if candidates else None,
        "positive_net_ev_after_cost": bool(candidates),
        "oos_predictions": predictions,
        "oos_prediction_count": len(predictions),
        "oos_blocker_reason": blocker_reason,
        "hard_conclusion": (
            "probability_edge_candidate_categories_found"
            if positive_categories
            else f"no_probability_edge_yet_{blocker_reason}"
            if blocker_reason
            else "no_probability_edge_yet"
        ),
        "model": model,
        "candidates": candidates,
    }


__all__ = ["SCHEMA_VERSION", "build_probability_edge_model_report", "load_jsonl", "write_json", "write_jsonl"]
