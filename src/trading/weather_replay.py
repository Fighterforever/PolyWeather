from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_execution_sim import binary_fill_pnl, simulate_taker_buy
from src.trading.weather_paper_journal import _safe_float
from src.weather.weather_sources import parse_utc


REPLAY_SCHEMA_VERSION = "polyweather_weather_replay.v1"


def _is_visible(row: Dict[str, Any], replay_time: str, *fields: str) -> bool:
    replay_dt = parse_utc(replay_time)
    if replay_dt is None:
        raise ValueError("replay_time must be an ISO UTC timestamp")
    for field in fields:
        raw = row.get(field)
        if raw is None:
            continue
        dt = parse_utc(raw)
        if dt is None:
            continue
        return dt <= replay_dt
    return True


def _latest_visible_orderbooks(
    orderbook_snapshots: Iterable[Dict[str, Any]],
    *,
    replay_time: str,
) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    latest_time: Dict[str, str] = {}
    for snapshot in orderbook_snapshots:
        if not isinstance(snapshot, dict):
            continue
        token_id = str(snapshot.get("token_id") or "").strip()
        recorded_at = str(snapshot.get("recorded_at") or "")
        if not token_id or not _is_visible(snapshot, replay_time, "recorded_at", "available_at"):
            continue
        if token_id not in latest_time or recorded_at > latest_time[token_id]:
            latest[token_id] = snapshot
            latest_time[token_id] = recorded_at
    return latest


def _payout_by_token(resolved_outcomes: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    payouts: Dict[str, float] = {}
    for row in resolved_outcomes:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "").strip()
        payout = _safe_float(row.get("payout"))
        if token_id and payout is not None:
            payouts[token_id] = float(payout)
    return payouts


def _brier(probabilities: List[float], outcomes: List[float]) -> Optional[float]:
    if not probabilities:
        return None
    return round(sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(probabilities), 8)


def _log_loss(probabilities: List[float], outcomes: List[float]) -> Optional[float]:
    if not probabilities:
        return None
    eps = 1e-6
    total = 0.0
    for p, y in zip(probabilities, outcomes):
        clipped = min(1.0 - eps, max(eps, p))
        total += -(y * math.log(clipped) + (1.0 - y) * math.log(1.0 - clipped))
    return round(total / len(probabilities), 8)


def _max_drawdown(values: List[float]) -> float:
    peak = 0.0
    trough = 0.0
    max_dd = 0.0
    running = 0.0
    for value in values:
        running += value
        if running > peak:
            peak = running
            trough = running
        if running < trough:
            trough = running
            max_dd = min(max_dd, trough - peak)
    return round(max_dd, 8)


def replay_taker_candidates(
    *,
    candidates: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    resolved_outcomes: Iterable[Dict[str, Any]],
    replay_time: str,
    size: float = 1.0,
) -> Dict[str, Any]:
    candidate_rows = [row for row in candidates if isinstance(row, dict)]
    visible_books = _latest_visible_orderbooks(orderbook_snapshots, replay_time=replay_time)
    payouts = _payout_by_token(resolved_outcomes)
    fills: List[Dict[str, Any]] = []
    skipped_future_candidate = 0
    no_visible_orderbook = 0
    missing_resolution = 0
    missed_fill = 0
    pnl_values: List[float] = []
    probabilities: List[float] = []
    outcomes: List[float] = []

    for candidate in candidate_rows:
        if not _is_visible(candidate, replay_time, "generated_at", "recorded_at", "available_at"):
            skipped_future_candidate += 1
            continue
        token_id = str(candidate.get("token_id") or "").strip()
        orderbook = visible_books.get(token_id)
        if not token_id or orderbook is None:
            no_visible_orderbook += 1
            continue
        fill = simulate_taker_buy(candidate=candidate, orderbook_snapshot=orderbook, size=size)
        if fill.get("missed_fill"):
            missed_fill += 1
        payout = payouts.get(token_id)
        if payout is None:
            missing_resolution += 1
        pnl = binary_fill_pnl(fill, payout=payout)
        record = {**fill, **pnl}
        fills.append(record)
        if pnl.get("pnl_usdc") is not None:
            pnl_values.append(float(pnl["pnl_usdc"]))
        probability = _safe_float(candidate.get("p_lcb") or candidate.get("p_model") or candidate.get("model_probability"))
        if probability is not None and payout is not None:
            probabilities.append(max(0.0, min(1.0, float(probability))))
            outcomes.append(1.0 if payout >= 0.999 else 0.0)

    fill_count = len(fills)
    resolved_pnl_usdc = round(sum(pnl_values), 8) if pnl_values else None
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "replay_time": replay_time,
        "candidate_count": len(candidate_rows),
        "fill_count": fill_count,
        "fill_rate": round(fill_count / max(1, fill_count + no_visible_orderbook), 6),
        "missed_fill_count": missed_fill,
        "missing_resolution_count": missing_resolution,
        "skipped_future_candidate_count": skipped_future_candidate,
        "no_visible_orderbook_count": no_visible_orderbook,
        "resolved_pnl_usdc": resolved_pnl_usdc,
        "resolved_pnl_cents": round(resolved_pnl_usdc * 100.0, 6) if resolved_pnl_usdc is not None else None,
        "drawdown_usdc": _max_drawdown(pnl_values) if pnl_values else None,
        "brier_score": _brier(probabilities, outcomes),
        "log_loss": _log_loss(probabilities, outcomes),
        "markout_count": 0,
        "fills": fills,
    }
