from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_live_impact_simulator.v1"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = str(row.get("token_id") or "")
        when = str(row.get("update_time") or row.get("generated_at") or "")
        if token and when >= str(latest.get(token, {}).get("update_time") or latest.get(token, {}).get("generated_at") or ""):
            latest[token] = row
    return latest


def build_weather_lp_live_impact_simulator_report(
    *,
    manual_order_rows: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    latest_by_token = _latest_by_token(quote_updates)
    rows: List[Dict[str, Any]] = []
    for row in manual_order_rows:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "")
        latest = latest_by_token.get(token_id, {})
        bid = _safe_float(latest.get("current_best_bid"))
        ask = _safe_float(latest.get("current_best_ask"))
        pre_mid = _safe_float(latest.get("current_midpoint"))
        quote_price = _safe_float(row.get("suggested_quote_price")) or 0.0
        quote_size = _safe_float(row.get("suggested_quote_size")) or 0.0
        crosses = bool(ask is not None and quote_price >= ask)
        resting = not crosses and quote_price > 0
        post_mid = pre_mid
        if resting and bid is not None and ask is not None and quote_price > bid:
            post_mid = round((quote_price + ask) / 2.0, 8)
        midpoint_change = None
        if pre_mid is not None and post_mid is not None:
            midpoint_change = round((post_mid - pre_mid) * 100.0, 8)
        pre_share = _safe_float(row.get("visible_reward_share_proxy"))
        post_share = pre_share
        if pre_share is not None and resting:
            # Visible-share impact is a proxy: adding size can improve our Q but may also change the midpoint.
            post_share = min(1.0, round(pre_share * 1.05, 8))
        warning = None
        if crosses:
            warning = "quote_would_cross_or_take"
        elif midpoint_change is not None and abs(midpoint_change) > 1.0:
            warning = "quote_changes_midpoint_by_more_than_1c"
        elif pre_mid is None:
            warning = "missing_current_orderbook"
        rows.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "market_slug": row.get("market_slug"),
                "token_id": token_id,
                "city": row.get("city"),
                "side": row.get("side"),
                "pre_quote_midpoint": pre_mid,
                "post_quote_midpoint_if_inserted": post_mid,
                "pre_visible_share_proxy": pre_share,
                "post_visible_share_proxy": post_share,
                "pre_q_min_proxy": None,
                "post_q_min_proxy": None,
                "quote_would_change_midpoint": bool(midpoint_change not in (None, 0.0)),
                "quote_would_improve_reward_score": bool(post_share is not None and pre_share is not None and post_share > pre_share),
                "quote_would_cross_or_take": crosses,
                "quote_would_be_resting": resting,
                "distance_from_midpoint": row.get("quote_distance_from_midpoint"),
                "qualifies_for_reward_after_insert": bool(row.get("qualifies_for_reward") and resting),
                "capital_at_risk": row.get("capital_at_risk"),
                "max_loss_if_filled": row.get("max_loss_if_filled"),
                "adverse_selection_risk_score": abs(float(midpoint_change or 0.0)),
                "impact_warning": warning,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    accepted = [row for row in rows if row.get("quote_would_be_resting") and not row.get("impact_warning")]
    return {
        "schema_version": SCHEMA_VERSION,
        "manual_quote_count": len(rows),
        "impact_simulation_ready": bool(rows),
        "accepted_resting_quote_count": len(accepted),
        "rejected_crossing_quote_count": len([row for row in rows if row.get("quote_would_cross_or_take")]),
        "midpoint_warning_count": len([row for row in rows if row.get("impact_warning") == "quote_changes_midpoint_by_more_than_1c"]),
        "missing_orderbook_count": len([row for row in rows if row.get("impact_warning") == "missing_current_orderbook"]),
        "total_capital_at_risk": round(sum(float(row.get("capital_at_risk") or 0.0) for row in rows), 8),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    text = source.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if isinstance(parsed, dict):
        rows = parsed.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed_line = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed_line, dict):
            rows.append(parsed_line)
    return rows


__all__ = ["SCHEMA_VERSION", "build_weather_lp_live_impact_simulator_report", "load_jsonl", "write_json", "write_jsonl"]
