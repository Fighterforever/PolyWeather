from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


WINDOW_ORDER = {"0-1m": 0, "1-3m": 1, "3-5m": 2, "5-15m": 3, "15-30m": 4}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry_price_band(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "price_unknown"
    if price <= 0.80:
        return "price_le_0_80"
    if price <= 0.95:
        return "price_0_80_to_0_95"
    return "price_gt_0_95"


def _group(rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_text(row.get(field)) or "unknown"].append(row)
    output = []
    for key, group in sorted(grouped.items()):
        resolved = [row for row in group if row.get("trade_replay_pnl_cents") is not None]
        output.append(
            {
                field: key,
                "trade_proxy_candidate_count": len(group),
                "trade_proxy_pnl_cents": round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved), 6) if resolved else None,
            }
        )
    return output


def build_eq_dead_no_trade_replay_report(*, observation_lock_trade_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [
        row
        for row in observation_lock_trade_rows
        if isinstance(row, dict)
        and row.get("bucket_type") == "eq"
        and row.get("lock_state") == "eq_yes_dead_no_locked"
        and row.get("locked_side") == "NO"
    ]
    candidates = [row for row in rows if row.get("approximate_hit_price") is not None]
    first_by_signal: Dict[tuple[Any, Any, Any], Dict[str, Any]] = {}
    for row in candidates:
        key = (row.get("market_slug"), row.get("token_id"), row.get("replay_time"))
        current = first_by_signal.get(key)
        if current is None or WINDOW_ORDER.get(_text(row.get("window")), 999) < WINDOW_ORDER.get(_text(current.get("window")), 999):
            first_by_signal[key] = row
    deduped = list(first_by_signal.values())
    pnl_rows = [row for row in candidates if row.get("trade_replay_pnl_cents") is not None]
    deduped_pnl_rows = [row for row in deduped if row.get("trade_replay_pnl_cents") is not None]
    missing_direct_trade_count = len([row for row in rows if row.get("trade_count") == 0])
    report_rows = [
        {
            **row,
            "strategy_id": "eq_dead_no_lock",
            "entry_price_band": _entry_price_band(row.get("approximate_hit_price")),
            "counts_for_live_gate": False,
            "live_order_path": False,
            "can_count_as_real_pnl": False,
            "can_count_as_trade_proxy": bool(row.get("approximate_hit_price") is not None),
        }
        for row in rows
    ]
    top = sorted(
        [row for row in report_rows if row.get("approximate_hit_price") is not None],
        key=lambda row: float(row.get("trade_replay_pnl_cents") or 0.0),
        reverse=True,
    )[:20]
    return {
        "schema_version": "polyweather_eq_dead_no_trade_replay.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": {
            "breached_eq_signal_count": len(
                {
                    (row.get("market_slug"), row.get("token_id"), row.get("replay_time"))
                    for row in rows
                }
            ),
            "no_trade_count": missing_direct_trade_count,
            "trade_proxy_candidate_count": len(candidates),
            "trade_proxy_pnl_cents": round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in pnl_rows), 6) if pnl_rows else None,
            "deduped_trade_proxy_candidate_count": len(deduped),
            "deduped_trade_proxy_pnl_cents": round(sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in deduped_pnl_rows), 6) if deduped_pnl_rows else None,
            "by_station": _group(deduped, "station_code"),
            "by_time_to_close": _group(deduped, "time_to_close_bucket"),
            "by_entry_price_band": _group(
                [{**row, "entry_price_band": _entry_price_band(row.get("approximate_hit_price"))} for row in deduped],
                "entry_price_band",
            ),
            "top_samples": top,
            "missing_direct_trade_count": missing_direct_trade_count,
        },
        "rows": report_rows,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows
