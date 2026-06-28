from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


HORIZONS_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
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


def build_eq_dead_no_markouts(
    *,
    fills: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    horizons: Dict[str, int] | None = None,
) -> List[Dict[str, Any]]:
    horizons = horizons or HORIZONS_SECONDS
    snapshots = [row for row in orderbook_snapshots if isinstance(row, dict)]
    results: List[Dict[str, Any]] = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        fill_time = _parse_utc(fill.get("recorded_at"))
        token_id = str(fill.get("token_id") or "").strip()
        entry_price = _safe_float(fill.get("entry_price"))
        for horizon, seconds in horizons.items():
            matching = []
            for snapshot in snapshots:
                if str(snapshot.get("token_id") or "").strip() != token_id:
                    continue
                snap_time = _parse_utc(snapshot.get("recorded_at"))
                if fill_time is None or snap_time is None:
                    continue
                age = (snap_time - fill_time).total_seconds()
                if age >= float(seconds):
                    matching.append((age, snapshot))
            matching.sort(key=lambda item: item[0])
            if not matching:
                results.append(
                    {
                        "schema_version": "polyweather_eq_dead_no_markout.v1",
                        "fill_id": fill.get("fill_id"),
                        "strategy_id": "eq_dead_no_lock",
                        "horizon": horizon,
                        "entry_price": entry_price,
                        "exit_no_bid": None,
                        "markout_cents": None,
                        "available_snapshot_count": 0,
                        "missing_snapshot_reason": "missing_later_orderbook_snapshot",
                        "paper_only": True,
                        "counts_for_live_gate": False,
                        "live_order_path": False,
                    }
                )
                continue
            snapshot = matching[0][1]
            exit_bid = _safe_float(snapshot.get("best_bid"))
            markout = round((float(exit_bid) - float(entry_price)) * 100.0, 6) if exit_bid is not None and entry_price is not None else None
            results.append(
                {
                    "schema_version": "polyweather_eq_dead_no_markout.v1",
                    "fill_id": fill.get("fill_id"),
                    "strategy_id": "eq_dead_no_lock",
                    "horizon": horizon,
                    "entry_price": entry_price,
                    "exit_no_bid": exit_bid,
                    "markout_cents": markout,
                    "available_snapshot_count": len(matching),
                    "missing_snapshot_reason": None if markout is not None else "missing_exit_no_bid_or_entry_price",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
    return results


def build_eq_dead_no_markout_report(
    *,
    fills: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    fill_rows = [row for row in fills if isinstance(row, dict)]
    rows = build_eq_dead_no_markouts(fills=fill_rows, orderbook_snapshots=orderbook_snapshots)
    resolved = [row for row in rows if row.get("markout_cents") is not None]
    return {
        "schema_version": "polyweather_eq_dead_no_markout_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "status": "ready_waiting_for_paper_fills" if not fill_rows else "evaluated",
        "fill_count": len(fill_rows),
        "markout_row_count": len(rows),
        "available_markout_count": len(resolved),
        "missing_snapshot_count": len([row for row in rows if row.get("missing_snapshot_reason")]),
        "mean_markout_cents": round(sum(float(row["markout_cents"]) for row in resolved) / len(resolved), 6) if resolved else None,
        "rows": rows,
    }
