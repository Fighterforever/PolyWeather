from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_crypto_touch_watch_markout.v1"
DEFAULT_HORIZONS: Tuple[int, ...] = (300, 900, 3600, 21600, 86400)


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


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


def _watch_id(row: Dict[str, Any]) -> str:
    if row.get("watch_id"):
        return str(row["watch_id"])
    text = json.dumps(
        {
            "market_slug": row.get("market_slug"),
            "token_id": row.get("token_id"),
            "side": row.get("side"),
            "market_creation_time": row.get("market_creation_time"),
            "target_time": row.get("target_time"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "")
        if token_id:
            grouped[token_id].append(row)
    for members in grouped.values():
        members.sort(key=lambda row: str(row.get("timestamp") or row.get("recorded_at") or ""))
    return grouped


def _first_after(rows: List[Dict[str, Any]], entry_time: datetime, horizon: int) -> Optional[Dict[str, Any]]:
    target_ts = entry_time.timestamp() + int(horizon)
    for row in rows:
        ts = _parse_utc(row.get("timestamp") or row.get("recorded_at"))
        if ts is not None and ts.timestamp() >= target_ts:
            return row
    return None


def build_crypto_touch_watch_markout_report(
    *,
    watch_rows: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]] = (),
    price_rows: Iterable[Dict[str, Any]] = (),
    horizons: Tuple[int, ...] = DEFAULT_HORIZONS,
) -> Dict[str, Any]:
    watches = [row for row in watch_rows if isinstance(row, dict)]
    snapshot_by_token = _by_token(orderbook_snapshots)
    price_by_token = _by_token(price_rows)
    markouts: List[Dict[str, Any]] = []
    for watch in watches:
        token_id = str(watch.get("token_id") or "")
        entry_time = _parse_utc(
            watch.get("entry_time")
            or watch.get("recorded_at")
            or watch.get("watch_time")
            or watch.get("generated_at")
            or watch.get("timestamp")
        )
        entry_price = _safe_float(watch.get("q_effective") or watch.get("best_ask"))
        wid = _watch_id(watch)
        for horizon in horizons:
            row = {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "watch_id": wid,
                "market_slug": watch.get("market_slug"),
                "token_id": token_id,
                "asset": watch.get("asset"),
                "side": watch.get("side"),
                "horizon": horizon,
                "entry_reference_price": entry_price,
                "exit_bid_or_mid": None,
                "markout_cents": None,
                "missing_snapshot_reason": None,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
            if not token_id:
                row["missing_snapshot_reason"] = "missing_token_id"
                markouts.append(row)
                continue
            if entry_time is None:
                row["missing_snapshot_reason"] = "missing_entry_time"
                markouts.append(row)
                continue
            if entry_price is None:
                row["missing_snapshot_reason"] = "missing_entry_reference_price"
                markouts.append(row)
                continue
            future = _first_after(snapshot_by_token.get(token_id) or [], entry_time, horizon)
            source = "orderbook_snapshot"
            if future is None:
                future = _first_after(price_by_token.get(token_id) or [], entry_time, horizon)
                source = "price_row"
            if future is None:
                row["missing_snapshot_reason"] = "missing_later_snapshot"
                markouts.append(row)
                continue
            exit_price = _safe_float(future.get("best_bid") or future.get("price_mid") or future.get("price"))
            if exit_price is None:
                row["missing_snapshot_reason"] = "missing_exit_bid_or_mid"
                markouts.append(row)
                continue
            row.update(
                {
                    "exit_bid_or_mid": exit_price,
                    "exit_source": source,
                    "exit_time": future.get("timestamp") or future.get("recorded_at"),
                    "markout_cents": round((exit_price - entry_price) * 100.0, 6),
                }
            )
            markouts.append(row)
    available = [row for row in markouts if row.get("markout_cents") is not None]
    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "watch_count": len(watches),
        "markout_row_count": len(markouts),
        "available_markout_count": len(available),
        "mean_markout_cents": round(sum(float(row["markout_cents"]) for row in available) / len(available), 8) if available else None,
        "by_horizon": _mean_by(available, "horizon"),
        "by_asset": _mean_by(available, "asset"),
        "by_side": _mean_by(available, "side"),
        "missing_snapshot_reason_counts": _count_by(markouts, "missing_snapshot_reason"),
        "markout_status": "no_near_miss_watch_rows" if not watches else "missing_later_snapshots" if not available else "markout_available",
        "markouts": markouts,
    }
    return report


def _mean_by(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        markout = _safe_float(row.get("markout_cents"))
        if markout is None:
            continue
        grouped[str(row.get(key) or "missing")].append(markout)
    return [
        {
            key: bucket,
            "available_markout_count": len(values),
            "mean_markout_cents": round(sum(values) / len(values), 8),
        }
        for bucket, values in sorted(grouped.items())
    ]


def _count_by(rows: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "none")
        counts[value] = counts.get(value, 0) + 1
    return [{"reason": reason, "count": counts[reason]} for reason in sorted(counts)]


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


__all__ = [
    "DEFAULT_HORIZONS",
    "SCHEMA_VERSION",
    "build_crypto_touch_watch_markout_report",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
