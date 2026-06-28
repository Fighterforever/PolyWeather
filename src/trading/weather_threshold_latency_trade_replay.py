from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_trade_tape_backfill import (
    locked_side_token_id,
    token_id_by_outcome,
    write_json,
    write_jsonl,
)
from src.weather.weather_sources import parse_utc


SCHEMA_VERSION = "polyweather_threshold_latency_trade_replay.v1"
WINDOWS: Tuple[Tuple[str, int], ...] = (("+1m", 1), ("+3m", 3), ("+5m", 5), ("+15m", 15))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _closed_by_slug(closed_markets: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for row in closed_markets:
        if not isinstance(row, dict):
            continue
        slug = _text(row.get("market_slug") or row.get("slug"))
        if slug and slug not in selected:
            selected[slug] = row
    return selected


def _trade_index(trades: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in trades:
        if not isinstance(row, dict):
            continue
        slug = _text(row.get("market_slug"))
        token = _text(row.get("token_id") or row.get("asset"))
        if slug and token:
            grouped[(slug, token)].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: parse_utc(row.get("timestamp")) or datetime.max.replace(tzinfo=timezone.utc))
    return grouped


def _observations_by_station_date(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = _text(row.get("station_code")).upper()
        target_date = _text(row.get("target_date_local") or row.get("target_date"))
        source = _text(row.get("settlement_source") or row.get("source")).lower()
        if source.startswith("aviationweather_metar"):
            source = "metar"
        if station and target_date:
            grouped[(station, target_date, source or "metar")].append(row)
    for obs_rows in grouped.values():
        obs_rows.sort(key=lambda row: parse_utc(row.get("available_at")) or datetime.max.replace(tzinfo=timezone.utc))
    return grouped


def _payout_for_side(record: Dict[str, Any], side: str) -> Optional[float]:
    yes = _safe_float(record.get("settled_yes_payout"))
    if yes is None:
        probabilities = record.get("settled_probability_by_outcome")
        if isinstance(probabilities, dict):
            yes = _safe_float(probabilities.get("Yes") if "Yes" in probabilities else probabilities.get("yes"))
    if yes is None:
        return None
    return yes if side.upper() == "YES" else 1.0 - yes


def _signal_side(signal_type: str) -> Optional[str]:
    if signal_type in {"ge_just_crossed", "ge_near_cross"}:
        return "YES"
    if signal_type in {"le_just_broken", "le_near_break"}:
        return "NO"
    return None


def _max_entry(signal_type: str) -> float:
    if signal_type in {"ge_just_crossed", "le_just_broken"}:
        return 0.995
    return 0.65


def _time_to_close_bucket(minutes: Optional[float]) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 30:
        return "lt_30m"
    if minutes < 120:
        return "30m_to_2h"
    return "gt_2h"


def _market_close(record: Dict[str, Any]) -> Optional[datetime]:
    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    for field in ("market_close_time", "end_time", "endDate", "closed_at"):
        parsed = parse_utc(spec.get(field) if field in spec else record.get(field))
        if parsed is not None:
            return parsed
    return None


def _events_for_record(
    record: Dict[str, Any],
    observations_by_key: Dict[Tuple[str, str, str], List[Dict[str, Any]]],
    *,
    near_margin_c: float,
) -> List[Dict[str, Any]]:
    station = _text(record.get("station_code")).upper()
    target_date = _text(record.get("target_date"))
    source = _text(record.get("settlement_source")).lower() or "metar"
    bucket = _text(record.get("bucket_type")).lower()
    threshold = _safe_float(record.get("threshold"))
    if bucket not in {"ge", "le"} or threshold is None:
        return []
    obs_rows = observations_by_key.get((station, target_date, source)) or observations_by_key.get((station, target_date, "metar")) or []
    events: List[Dict[str, Any]] = []
    current_high: Optional[float] = None
    previous_high: Optional[float] = None
    seen: set[Tuple[str, str]] = set()
    for obs in obs_rows:
        available_at = _text(obs.get("available_at"))
        observed = _safe_float(obs.get("temperature_c") or obs.get("temperature"))
        if not available_at or observed is None:
            continue
        previous_high = current_high
        current_high = observed if current_high is None else max(current_high, observed)
        signal_type: Optional[str] = None
        if bucket == "ge":
            if previous_high is not None and previous_high < threshold <= current_high:
                signal_type = "ge_just_crossed"
            elif current_high < threshold and threshold - current_high <= near_margin_c:
                signal_type = "ge_near_cross"
        elif bucket == "le":
            if previous_high is not None and previous_high <= threshold < current_high:
                signal_type = "le_just_broken"
            elif current_high <= threshold and threshold - current_high <= near_margin_c:
                signal_type = "le_near_break"
        if not signal_type:
            continue
        event_key = (signal_type, available_at)
        if event_key in seen:
            continue
        seen.add(event_key)
        close_dt = _market_close(record)
        signal_dt = parse_utc(available_at)
        time_to_close = (
            round((close_dt - signal_dt).total_seconds() / 60.0, 3)
            if close_dt is not None and signal_dt is not None
            else None
        )
        events.append(
            {
                "market_slug": record.get("market_slug"),
                "market_id": record.get("market_id"),
                "station_code": station,
                "target_date": target_date,
                "settlement_source": source,
                "bucket_type": bucket,
                "threshold": threshold,
                "signal_type": signal_type,
                "signal_time": available_at,
                "previous_high": previous_high,
                "current_high": current_high,
                "locked_side": _signal_side(signal_type),
                "time_to_close_minutes": time_to_close,
                "time_to_close_bucket": _time_to_close_bucket(time_to_close),
            }
        )
    return events


def _count_by(rows: Iterable[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    counts = Counter(_text(row.get(field)) or "unknown" for row in rows)
    return [{field: key, "count": count} for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]


def _proxy_summary(rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_text(row.get(field)) or "unknown"].append(row)
    output: List[Dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        candidates = [row for row in group if row.get("approximate_hit_price") is not None]
        resolved = [row for row in candidates if row.get("trade_replay_pnl_cents") is not None]
        pnl = sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved)
        output.append(
            {
                field: key,
                "row_count": len(group),
                "trade_proxy_candidate_count": len(candidates),
                "trade_proxy_positive_count": len(
                    [row for row in resolved if float(row.get("trade_replay_pnl_cents") or 0.0) > 0.0]
                ),
                "trade_proxy_pnl_cents": round(pnl, 6) if resolved else None,
            }
        )
    return output


def build_threshold_latency_trade_replay_report(
    *,
    closed_markets: Iterable[Dict[str, Any]],
    observations: Iterable[Dict[str, Any]],
    trade_tape_rows: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
    near_margin_c: float = 1.0,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    records = [
        row
        for row in closed_markets
        if isinstance(row, dict)
        and _text(row.get("market_family")).lower() == "temperature"
        and _text(row.get("settlement_source")).lower() == "metar"
        and _text(row.get("bucket_type")).lower() in {"ge", "le"}
    ]
    obs_by_key = _observations_by_station_date(observations)
    trades_by_market_token = _trade_index(trade_tape_rows)
    all_events: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for record in records:
        events = _events_for_record(record, obs_by_key, near_margin_c=near_margin_c)
        all_events.extend(events)
        token_map = token_id_by_outcome(record)
        for event in events:
            side = _text(event.get("locked_side")).upper()
            token = token_map.get("Yes" if side == "YES" else "No")
            if not token:
                token = locked_side_token_id(event, record)
            payout = _payout_for_side(record, side)
            signal_dt = parse_utc(event.get("signal_time"))
            source_trades = trades_by_market_token.get((_text(record.get("market_slug")), token or ""), [])
            for window, minutes in WINDOWS:
                reason = "ok"
                matching: List[Dict[str, Any]] = []
                if signal_dt is None:
                    reason = "missing_signal_time"
                elif not token:
                    reason = "missing_signal_side_token"
                elif not source_trades:
                    reason = "missing_same_token_trade"
                else:
                    end = signal_dt + timedelta(minutes=minutes)
                    for trade in source_trades:
                        trade_dt = parse_utc(trade.get("timestamp"))
                        if trade_dt is not None and signal_dt <= trade_dt <= end:
                            matching.append(trade)
                    if not matching:
                        reason = "no_trade_after_event"
                max_entry = _max_entry(_text(event.get("signal_type")))
                at_or_better = [
                    trade
                    for trade in matching
                    if _safe_float(trade.get("price")) is not None and float(trade.get("price")) <= max_entry
                ]
                if matching and not at_or_better:
                    reason = "trade_above_max_entry_price"
                hit_trade = at_or_better[0] if at_or_better else None
                hit_price = _safe_float((hit_trade or {}).get("price"))
                pnl = round((float(payout) - float(hit_price)) * 100.0, 6) if payout is not None and hit_price is not None else None
                rows.append(
                    {
                        "schema_version": f"{SCHEMA_VERSION}.row",
                        "paper_only": True,
                        "counts_for_live_gate": False,
                        "live_order_path": False,
                        **event,
                        "token_id": token,
                        "window": window,
                        "trade_count": len(matching),
                        "first_trade_at": matching[0].get("timestamp") if matching else None,
                        "max_entry_price_for_signal_side": max_entry,
                        "approximate_hit_price": hit_price,
                        "payout": payout,
                        "trade_replay_pnl_cents": pnl,
                        "can_count_as_real_pnl": False,
                        "can_count_as_trade_proxy": hit_price is not None,
                        "reason": reason,
                        "trade_direction_confidence": (hit_trade or {}).get("direction_confidence"),
                        "trade_source_trade_id": (hit_trade or {}).get("source_trade_id"),
                    }
                )
    candidates = [row for row in rows if row.get("approximate_hit_price") is not None]
    resolved = [row for row in candidates if row.get("trade_replay_pnl_cents") is not None]
    total_pnl = sum(float(row.get("trade_replay_pnl_cents") or 0.0) for row in resolved)
    crossing_events = [row for row in all_events if row.get("signal_type") in {"ge_just_crossed", "le_just_broken"}]
    near_events = [row for row in all_events if row.get("signal_type") in {"ge_near_cross", "le_near_break"}]
    event_ids_with_trade = {
        (row.get("market_slug"), row.get("signal_time"), row.get("signal_type"))
        for row in rows
        if int(row.get("trade_count") or 0) > 0
    }
    summary = {
        "schema_version": f"{SCHEMA_VERSION}.summary",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "update_event_count": len(all_events),
        "crossing_event_count": len(crossing_events),
        "near_cross_event_count": len(near_events),
        "trade_after_event_count": len(event_ids_with_trade),
        "trade_proxy_candidate_count": len(candidates),
        "trade_proxy_positive_count": len(
            [row for row in resolved if float(row.get("trade_replay_pnl_cents") or 0.0) > 0.0]
        ),
        "trade_proxy_pnl_cents": round(total_pnl, 6) if resolved else None,
        "missing_trade_count": len([row for row in rows if int(row.get("trade_count") or 0) == 0]),
        "by_station": _proxy_summary(rows, "station_code"),
        "by_bucket_type": _proxy_summary(rows, "bucket_type"),
        "by_window": _proxy_summary(rows, "window"),
        "by_time_to_close": _proxy_summary(rows, "time_to_close_bucket"),
        "reason_counts": _count_by(rows, "reason"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": summary,
        "events": all_events,
        "rows": rows,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


__all__ = [
    "SCHEMA_VERSION",
    "WINDOWS",
    "build_threshold_latency_trade_replay_report",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
