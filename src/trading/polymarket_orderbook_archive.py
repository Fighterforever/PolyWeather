from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_paper_journal import (
    _append_jsonl,
    _safe_float,
    stable_json_hash,
    utc_now_iso,
)


ORDERBOOK_ARCHIVE_SCHEMA_VERSION = "polyweather_polymarket_orderbook_archive.v1"
ORDERBOOK_SNAPSHOT_SCHEMA_VERSION = "polyweather_polymarket_orderbook_snapshot.v1"
DEFAULT_ORDERBOOK_ARCHIVE_DIR = Path("data/trading/polymarket_orderbooks")


def _parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        if number > 10_000_000_000:
            number = number / 1000.0
        return datetime.fromtimestamp(number, timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latency_ms(recorded_at: str, book_timestamp: Any) -> Optional[int]:
    recorded = _parse_utc(recorded_at)
    book_time = _parse_utc(book_timestamp)
    if recorded is None or book_time is None:
        return None
    return max(0, int((recorded - book_time).total_seconds() * 1000))


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_market_close_time(row: Dict[str, Any]) -> Optional[datetime]:
    settlement_spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    for value in (
        settlement_spec.get("market_close_time"),
        settlement_spec.get("end_time"),
        row.get("market_close_time"),
        row.get("end_time"),
        row.get("endTime"),
        row.get("end_date"),
        row.get("endDate"),
    ):
        parsed = _parse_utc(value)
        if parsed is not None:
            return parsed
    return None


def _levels(order_book: Dict[str, Any], side: str) -> List[Dict[str, float]]:
    field = "ask_ladder" if side == "ask" else "bid_ladder"
    raw = order_book.get(field)
    if raw is None:
        raw = order_book.get("asks" if side == "ask" else "bids")
    rows: List[Dict[str, float]] = []
    if not isinstance(raw, list):
        return rows
    for level in raw:
        if not isinstance(level, dict):
            continue
        price = _safe_float(level.get("price"))
        size = _safe_float(level.get("size"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        rows.append({"price": float(price), "size": float(size)})
    reverse = side == "bid"
    return sorted(rows, key=lambda item: item["price"], reverse=reverse)


def estimate_taker_effective_price(
    order_book: Dict[str, Any],
    *,
    side: str = "buy",
    size: float = 1.0,
) -> Dict[str, Any]:
    """Walk visible book depth for a taker trade in share units."""

    desired_size = max(0.0, float(size))
    levels = _levels(order_book, "ask" if side == "buy" else "bid")
    remaining = desired_size
    filled = 0.0
    notional = 0.0
    consumed: List[Dict[str, float]] = []
    for level in levels:
        if remaining <= 0:
            break
        take_size = min(remaining, float(level["size"]))
        filled += take_size
        notional += take_size * float(level["price"])
        remaining -= take_size
        consumed.append({"price": float(level["price"]), "size": round(take_size, 8)})
    return {
        "requested_size": desired_size,
        "filled_size": round(filled, 8),
        "unfilled_size": round(max(0.0, remaining), 8),
        "avg_price": round(notional / filled, 8) if filled > 0 else None,
        "notional_usdc": round(notional, 8),
        "levels_consumed": consumed,
        "fully_filled": remaining <= 1e-12,
    }


def build_orderbook_snapshot_records(
    rows: Iterable[Dict[str, Any]],
    *,
    recorded_at: Optional[str] = None,
    source_snapshot_id: Optional[str] = None,
    source: str = "polymarket_readonly",
    taker_probe_size: float = 1.0,
) -> List[Dict[str, Any]]:
    recorded_at = recorded_at or utc_now_iso()
    records: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        order_book = row.get("order_book") if isinstance(row.get("order_book"), dict) else {}
        token_id = str(row.get("token_id") or order_book.get("token_id") or "").strip()
        if not token_id or not order_book:
            continue
        settlement_spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else None
        market_bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else None
        market_close_time = _row_market_close_time(row)
        observation_window_end_time = (settlement_spec or {}).get("observation_window_end_time") or row.get(
            "observation_window_end_time"
        )
        settlement_due_time = (settlement_spec or {}).get("settlement_due_time") or row.get("settlement_due_time")
        identity = {
            "source_snapshot_id": source_snapshot_id,
            "recorded_at": recorded_at,
            "market_slug": row.get("market_slug"),
            "token_id": token_id,
            "side": row.get("side"),
        }
        record = {
            "schema_version": ORDERBOOK_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": stable_json_hash(identity, length=24),
            "recorded_at": recorded_at,
            "paper_only": True,
            "counts_for_live_gate": False,
            "source": source,
            "source_snapshot_id": source_snapshot_id,
            "market_id": row.get("market_id"),
            "market_slug": row.get("market_slug"),
            "event_slug": row.get("event_slug"),
            "question": row.get("question"),
            "city": row.get("city"),
            "market_family": row.get("market_family"),
            "bucket_label": row.get("bucket_label"),
            "bucket_type": row.get("bucket_type") or (market_bucket or {}).get("bucket_type"),
            "threshold": _safe_float(row.get("threshold") or (market_bucket or {}).get("threshold")),
            "unit": row.get("unit") or (market_bucket or {}).get("unit"),
            "target_date": row.get("target_date") or (settlement_spec or {}).get("target_date"),
            "end_date": _iso_utc(market_close_time) if market_close_time is not None else row.get("end_date"),
            "end_time": _iso_utc(market_close_time) if market_close_time is not None else row.get("end_time"),
            "market_close_time": (
                _iso_utc(market_close_time) if market_close_time is not None else row.get("market_close_time")
            ),
            "observation_window_end_time": observation_window_end_time,
            "settlement_due_time": settlement_due_time,
            "settlement_grace_hours": (settlement_spec or {}).get("settlement_grace_hours")
            or row.get("settlement_grace_hours"),
            "settlement_spec_status": row.get("settlement_spec_status") or (settlement_spec or {}).get("status"),
            "settlement_rule_hash": row.get("settlement_rule_hash") or (settlement_spec or {}).get("rule_hash"),
            "settlement_station_code": row.get("settlement_station_code") or (settlement_spec or {}).get("station_code"),
            "settlement_source": row.get("settlement_source") or (settlement_spec or {}).get("settlement_source"),
            "settlement_spec": settlement_spec,
            "market_bucket": market_bucket,
            "token_id": token_id,
            "side": row.get("side"),
            "outcome": row.get("outcome"),
            "book_timestamp": order_book.get("timestamp"),
            "latency_ms": _latency_ms(recorded_at, order_book.get("timestamp")),
            "best_bid": _safe_float(order_book.get("best_bid") or row.get("best_bid")),
            "best_ask": _safe_float(order_book.get("best_ask") or row.get("best_ask")),
            "spread": _safe_float(order_book.get("spread") or row.get("spread")),
            "bid_depth_usdc_3c": _safe_float(order_book.get("bid_depth_usdc_3c")),
            "ask_depth_usdc_3c": _safe_float(order_book.get("ask_depth_usdc_3c")),
            "bid_ladder": _levels(order_book, "bid"),
            "ask_ladder": _levels(order_book, "ask"),
            "taker_buy_probe": estimate_taker_effective_price(
                order_book,
                side="buy",
                size=taker_probe_size,
            ),
            "taker_sell_probe": estimate_taker_effective_price(
                order_book,
                side="sell",
                size=taker_probe_size,
            ),
        }
        records.append(record)
    return records


def write_orderbook_archive_from_payload(
    payload: Dict[str, Any],
    *,
    archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    recorded_at: Optional[str] = None,
    taker_probe_size: float = 1.0,
) -> Dict[str, Any]:
    archive_root = Path(archive_dir)
    recorded_at = recorded_at or utc_now_iso()
    records = build_orderbook_snapshot_records(
        payload.get("rows") or [],
        recorded_at=recorded_at,
        source_snapshot_id=payload.get("snapshot_id"),
        source=str(payload.get("source") or "polymarket_readonly"),
        taker_probe_size=taker_probe_size,
    )
    written = _append_jsonl(archive_root / "orderbook_snapshots.jsonl", records)
    manifest = {
        "schema_version": ORDERBOOK_ARCHIVE_SCHEMA_VERSION,
        "archive_dir": str(archive_root),
        "recorded_at": recorded_at,
        "source_snapshot_id": payload.get("snapshot_id"),
        "source": payload.get("source"),
        "rows_seen": len(payload.get("rows") or []),
        "snapshot_count": len(records),
        "written_count": written,
        "paper_only": True,
        "counts_for_live_gate": False,
    }
    archive_root.mkdir(parents=True, exist_ok=True)
    (archive_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest
