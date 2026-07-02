from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json
from src.trading.polymarket_alpha.weather_lp_reward_risk import load_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_cancellation_policy.v1"
POLICIES = (
    "hold_full_window",
    "cancel_at_hour_boundary",
    "cancel_after_reward_disqualified",
    "cancel_after_5m_if_markout_negative",
    "cancel_after_15m_if_markout_negative",
    "cancel_when_midpoint_moves_against_quote_by_1c",
    "cancel_when_weather_peak_window_starts",
)


def _parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _updates_by_quote(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        if quote_id:
            buckets[quote_id].append(row)
    for bucket_rows in buckets.values():
        bucket_rows.sort(key=lambda row: _parse_utc(row.get("update_time") or row.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return buckets


def _select_update(policy: str, rows: List[Dict[str, Any]]) -> tuple[Optional[Dict[str, Any]], str]:
    if not rows:
        return None, "no_updates"
    if policy == "hold_full_window":
        return rows[-1], "manual_hold_latest_update"
    for row in rows:
        when = _parse_utc(row.get("update_time") or row.get("generated_at"))
        markout = _safe_float(row.get("price_markout_from_entry"))
        if policy == "cancel_at_hour_boundary" and when is not None and when.minute >= 58:
            return row, "near_hour_boundary"
        if policy == "cancel_after_reward_disqualified" and not row.get("qualifies_for_reward", row.get("still_qualifies_for_reward")):
            return row, str(row.get("non_qualification_reason") or "reward_disqualified")
        if policy == "cancel_after_5m_if_markout_negative" and when is not None and markout is not None and markout < 0 and _elapsed_seconds(rows, row) >= 300:
            return row, "5m_negative_markout"
        if policy == "cancel_after_15m_if_markout_negative" and when is not None and markout is not None and markout < 0 and _elapsed_seconds(rows, row) >= 900:
            return row, "15m_negative_markout"
        if policy == "cancel_when_midpoint_moves_against_quote_by_1c" and markout is not None and markout <= -1.0:
            return row, "midpoint_moved_against_quote_by_1c"
        if policy == "cancel_when_weather_peak_window_starts" and when is not None and 11 <= when.hour <= 16:
            return row, "weather_peak_risk_window_proxy"
    return rows[-1], "condition_not_hit_use_latest"


def _elapsed_seconds(rows: List[Dict[str, Any]], row: Dict[str, Any]) -> float:
    first = _parse_utc(rows[0].get("update_time") or rows[0].get("generated_at")) if rows else None
    current = _parse_utc(row.get("update_time") or row.get("generated_at"))
    if first is None or current is None:
        return 0.0
    return max(0.0, (current - first).total_seconds())


def build_weather_lp_cancellation_policy_report(
    *,
    quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    quote_rows = [row for row in quotes if isinstance(row, dict)]
    updates_by_quote = _updates_by_quote(quote_updates)
    rows: List[Dict[str, Any]] = []
    for quote in quote_rows:
        quote_id = str(quote.get("quote_id") or "")
        history = updates_by_quote.get(quote_id, [])
        start = _parse_utc(quote.get("quote_start_time"))
        for policy in POLICIES:
            selected, reason = _select_update(policy, history)
            when = _parse_utc((selected or {}).get("update_time") or (selected or {}).get("generated_at"))
            time_on_book = None
            if start is not None and when is not None:
                time_on_book = max(0.0, (when - start).total_seconds())
            reward = _safe_float((selected or {}).get("cumulative_reward_points_proxy"))
            markout = _safe_float((selected or {}).get("price_markout_from_entry"))
            reward_to_risk = None
            if reward is not None and markout not in (None, 0):
                reward_to_risk = round(reward / abs(float(markout)), 8)
            rows.append(
                {
                    "schema_version": f"{SCHEMA_VERSION}.row",
                    "quote_id": quote_id,
                    "city": quote.get("city"),
                    "strategy_variant": quote.get("strategy_variant"),
                    "policy": policy,
                    "time_on_book_seconds": time_on_book,
                    "reward_points_proxy": reward,
                    "markout": markout,
                    "adverse_selection": bool((selected or {}).get("adverse_selection_flag") or (selected or {}).get("adverse_selection")),
                    "cancellation_time": (selected or {}).get("update_time") or (selected or {}).get("generated_at"),
                    "cancellation_reason": reason,
                    "reward_to_risk_proxy": reward_to_risk,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    by_policy: List[Dict[str, Any]] = []
    hold = [row for row in rows if row.get("policy") == "hold_full_window"]
    hold_by_quote = {str(row.get("quote_id") or ""): row for row in hold}
    for policy in POLICIES:
        policy_rows = [row for row in rows if row.get("policy") == policy]
        markouts = [float(row.get("markout")) for row in policy_rows if row.get("markout") is not None]
        rewards = [float(row.get("reward_points_proxy")) for row in policy_rows if row.get("reward_points_proxy") is not None]
        negative_risk = sum(abs(value) for value in markouts if value < 0)
        by_policy.append(
            {
                "policy": policy,
                "quote_count": len(policy_rows),
                "mean_markout": round(sum(markouts) / len(markouts), 8) if markouts else None,
                "reward_points_proxy": round(sum(rewards), 8) if rewards else 0.0,
                "adverse_selection_count": len([row for row in policy_rows if row.get("adverse_selection")]),
                "reward_to_risk_proxy": round(sum(rewards) / negative_risk, 8) if rewards and negative_risk else None,
                "reward_points_lost_vs_hold": _policy_reward_delta(policy_rows, hold_by_quote),
                "markout_improvement_vs_hold": _policy_markout_delta(policy_rows, hold_by_quote),
                "net_proxy_score": round((sum(rewards) if rewards else 0.0) + (sum(markouts) if markouts else 0.0), 8),
            }
        )
    recommendation = _recommend_policy(by_policy)
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "quote_count": len(quote_rows),
        "policy_count": len(POLICIES),
        "by_policy": by_policy,
        "rows": rows,
        "cancellation_policy_recommendation": recommendation,
        "recommendation": recommendation,
    }


def _recommend_policy(by_policy: List[Dict[str, Any]]) -> str:
    scored = [
        row for row in by_policy
        if row.get("mean_markout") is not None and row.get("reward_points_proxy") is not None
    ]
    if not scored:
        return "insufficient_update_history"
    best = max(scored, key=lambda row: (float(row.get("reward_to_risk_proxy") or -999), float(row.get("mean_markout") or -999)))
    if best.get("policy") == "cancel_at_hour_boundary" and (best.get("mean_markout") or 0) >= 0:
        return "cancel_at_hour_boundary_reduces_risk"
    if best.get("reward_to_risk_proxy") is None:
        return "continue_collecting_policy_updates"
    if float(best.get("reward_to_risk_proxy") or 0) < 1:
        return "reward_does_not_cover_price_risk_yet"
    return str(best.get("policy"))


def _policy_reward_delta(policy_rows: List[Dict[str, Any]], hold_by_quote: Dict[str, Dict[str, Any]]) -> Optional[float]:
    deltas = []
    for row in policy_rows:
        hold = hold_by_quote.get(str(row.get("quote_id") or ""))
        if not hold:
            continue
        value = _safe_float(row.get("reward_points_proxy"))
        hold_value = _safe_float(hold.get("reward_points_proxy"))
        if value is not None and hold_value is not None:
            deltas.append(hold_value - value)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 8)


def _policy_markout_delta(policy_rows: List[Dict[str, Any]], hold_by_quote: Dict[str, Dict[str, Any]]) -> Optional[float]:
    deltas = []
    for row in policy_rows:
        hold = hold_by_quote.get(str(row.get("quote_id") or ""))
        if not hold:
            continue
        value = _safe_float(row.get("markout"))
        hold_value = _safe_float(hold.get("markout"))
        if value is not None and hold_value is not None:
            deltas.append(value - hold_value)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 8)


__all__ = ["SCHEMA_VERSION", "POLICIES", "build_weather_lp_cancellation_policy_report", "load_jsonl", "write_json"]
