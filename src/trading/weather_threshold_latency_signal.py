from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_observation_lock_signal import build_market_side_book_pair_index
from src.weather.metar_update_events import detect_high_update_events
from src.weather.weather_observations import (
    detect_station_observation_anomalies,
    load_intraday_observations,
)
from src.weather.weather_sources import parse_utc


SCHEMA_VERSION = "polyweather_threshold_latency_signal.v1"
DUST_PRICE_BUCKET = "price_lt_0_005"
SUPPORTED_SOURCES = {"metar"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _price_bucket(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "price_unknown"
    if price < 0.005:
        return DUST_PRICE_BUCKET
    if price < 0.03:
        return "price_0_005_to_0_03"
    return "price_ge_0_03"


def _spec(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}


def _first_text(row: Dict[str, Any], fields: Iterable[str]) -> str:
    spec = _spec(row)
    for field in fields:
        for source in (row, spec):
            value = _text(source.get(field))
            if value:
                return value
    return ""


def _first_float(row: Dict[str, Any], fields: Iterable[str]) -> Optional[float]:
    spec = _spec(row)
    bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}
    for field in fields:
        for source in (row, spec, bucket):
            value = _safe_float(source.get(field))
            if value is not None:
                return value
    return None


def _token_side(row: Dict[str, Any]) -> str:
    return _text(row.get("side") or row.get("outcome")).upper()


def _book_entry(pair: Dict[str, Any], side: str) -> Dict[str, Any]:
    return pair.get(side) if isinstance(pair.get(side), dict) else {}


def _book_value(row: Dict[str, Any], field: str) -> Optional[float]:
    value = _safe_float(row.get(field))
    if value is not None:
        return value
    book = row.get("order_book") if isinstance(row.get("order_book"), dict) else {}
    return _safe_float(book.get(field))


def _station_observation_rows(
    observations: Iterable[Dict[str, Any]],
    *,
    station_code: str,
    target_date: str,
    replay_time: str,
    settlement_source: str,
) -> List[Dict[str, Any]]:
    replay_dt = parse_utc(replay_time)
    if replay_dt is None:
        return []
    rows: List[Dict[str, Any]] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        if _text(row.get("station_code")).upper() != station_code.upper():
            continue
        if _text(row.get("target_date_local") or row.get("target_date")) != target_date:
            continue
        source = _text(row.get("settlement_source") or row.get("source")).lower()
        wanted = settlement_source.lower()
        if source != wanted and not (wanted == "metar" and source.startswith("aviationweather_metar")):
            continue
        available_at = parse_utc(row.get("available_at"))
        if available_at is None or available_at > replay_dt:
            continue
        rows.append(row)
    return rows


def _signal_type(
    *,
    bucket_type: str,
    threshold: Optional[float],
    previous_high: Optional[float],
    current_high: Optional[float],
    time_since_available_seconds: Optional[int],
    max_update_age_minutes: float,
    near_cross_margin_c: float,
    near_break_margin_c: float,
) -> Optional[str]:
    if threshold is None or current_high is None:
        return None
    fresh_update = (
        time_since_available_seconds is not None
        and time_since_available_seconds <= float(max_update_age_minutes) * 60.0
    )
    if bucket_type == "ge":
        if previous_high is not None and previous_high < threshold <= current_high and fresh_update:
            return "ge_just_crossed"
        if current_high < threshold and threshold - current_high <= float(near_cross_margin_c):
            return "ge_near_cross"
    if bucket_type == "le":
        if previous_high is not None and previous_high <= threshold < current_high and fresh_update:
            return "le_just_broken"
        if current_high <= threshold and threshold - current_high <= float(near_break_margin_c):
            return "le_near_break"
    return None


def _side_for_signal(signal_type: Optional[str]) -> Optional[str]:
    if signal_type in {"ge_just_crossed", "ge_near_cross"}:
        return "YES"
    if signal_type in {"le_just_broken", "le_near_break"}:
        return "NO"
    return None


def _fair_probability(
    *,
    signal_type: Optional[str],
    near_cross_probability: float,
    just_crossed_probability: float,
) -> Optional[float]:
    if signal_type in {"ge_just_crossed", "le_just_broken"}:
        return float(just_crossed_probability)
    if signal_type in {"ge_near_cross", "le_near_break"}:
        return float(near_cross_probability)
    return None


def build_threshold_latency_signal_report(
    rows: Iterable[Dict[str, Any]],
    *,
    observations: Optional[Iterable[Dict[str, Any]]] = None,
    intraday_observation_path: Optional[str | Path] = None,
    generated_at: Optional[str] = None,
    near_cross_margin_c: float = 1.0,
    near_break_margin_c: float = 1.0,
    max_update_age_minutes: float = 5.0,
    near_cross_probability: float = 0.65,
    just_crossed_probability: float = 0.995,
    settlement_cost: float = 0.005,
    max_spread: float = 0.03,
    min_ask_depth: float = 1.0,
) -> Dict[str, Any]:
    generated_at = generated_at or _utc_now_iso()
    source_rows = [row for row in rows if isinstance(row, dict)]
    obs_rows = list(observations) if observations is not None else load_intraday_observations(intraday_observation_path or "")
    pair_index = build_market_side_book_pair_index(source_rows)
    signal_rows: List[Dict[str, Any]] = []
    event_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in source_rows:
        bucket_type = _text(_first_text(row, ("bucket_type",))).lower()
        threshold = _first_float(row, ("threshold",))
        station_code = _text(_first_text(row, ("station_code", "settlement_station_code"))).upper()
        target_date = _text(_first_text(row, ("target_date",)))
        settlement_source = _text(_first_text(row, ("settlement_source",))).lower()
        cache_key = (station_code, target_date, settlement_source)
        if cache_key not in event_cache:
            event_cache[cache_key] = detect_high_update_events(
                station_code=station_code,
                target_date=target_date,
                replay_time=generated_at,
                observations=obs_rows,
                settlement_source=settlement_source or "metar",
                thresholds=[threshold] if threshold is not None else [],
            )
        event = event_cache[cache_key]
        station_obs = _station_observation_rows(
            obs_rows,
            station_code=station_code,
            target_date=target_date,
            replay_time=generated_at,
            settlement_source=settlement_source or "metar",
        )
        anomaly_flags = detect_station_observation_anomalies(station_obs)
        signal_type = _signal_type(
            bucket_type=bucket_type,
            threshold=threshold,
            previous_high=event.get("previous_high"),
            current_high=event.get("current_high"),
            time_since_available_seconds=event.get("update_age_seconds"),
            max_update_age_minutes=max_update_age_minutes,
            near_cross_margin_c=near_cross_margin_c,
            near_break_margin_c=near_break_margin_c,
        )
        side_to_buy = _side_for_signal(signal_type)
        pair = pair_index.get(_text(row.get("market_slug") or row.get("market_id"))) or {}
        direct_entry = _book_entry(pair, side_to_buy or "")
        row_is_side = side_to_buy is not None and _token_side(row) == side_to_buy
        if not direct_entry and row_is_side:
            direct_entry = {
                "token_id": row.get("token_id"),
                "best_ask": _book_value(row, "best_ask"),
                "best_bid": _book_value(row, "best_bid"),
                "spread": _book_value(row, "spread"),
                "ask_depth": _book_value(row, "ask_depth_usdc_3c"),
            }
        best_ask = _safe_float(direct_entry.get("best_ask"))
        ask_depth = _safe_float(direct_entry.get("ask_depth"))
        spread = _safe_float(direct_entry.get("spread"))
        fair_probability = _fair_probability(
            signal_type=signal_type,
            near_cross_probability=near_cross_probability,
            just_crossed_probability=just_crossed_probability,
        )
        latency_edge = (
            round(float(fair_probability) - float(best_ask) - float(settlement_cost), 6)
            if fair_probability is not None and best_ask is not None
            else None
        )
        price_bucket = _price_bucket(best_ask)
        blockers: List[str] = []
        if bucket_type not in {"ge", "le"}:
            blockers.append("bucket_type_not_alpha")
        if not signal_type:
            blockers.append("no_threshold_latency_signal")
        if settlement_source not in SUPPORTED_SOURCES:
            blockers.append("unsupported_official_source_not_alpha")
        if event.get("current_high") is None:
            blockers.append("missing_current_high")
        if event.get("previous_high") is None:
            blockers.append("missing_previous_high")
        if event.get("update_age_seconds") is None or event.get("update_age_seconds") > float(max_update_age_minutes) * 60.0:
            blockers.append("stale_metar_update")
        if best_ask is None:
            blockers.append("missing_direct_ask")
        if ask_depth is None:
            blockers.append("missing_ask_depth")
        elif ask_depth < float(min_ask_depth):
            blockers.append("ask_depth_too_low")
        if spread is None:
            blockers.append("missing_spread")
        elif spread > float(max_spread):
            blockers.append("spread_too_wide")
        if price_bucket == DUST_PRICE_BUCKET:
            blockers.append("dust_price")
        if latency_edge is None or latency_edge <= 0:
            blockers.append("latency_edge_not_positive")
        if anomaly_flags:
            blockers.append("blocked_by_observation_anomaly")
        decision = "candidate" if not blockers else "watch" if signal_type else "reject"
        signal_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "market_slug": row.get("market_slug"),
                "token_id": direct_entry.get("token_id") or row.get("token_id"),
                "bucket_type": bucket_type,
                "threshold": threshold,
                "station_code": station_code or None,
                "target_date": target_date or None,
                "settlement_source": settlement_source or None,
                "previous_high": event.get("previous_high"),
                "current_high": event.get("current_high"),
                "previous_observation_at": event.get("previous_observation_at"),
                "current_observation_at": event.get("observation_at"),
                "current_available_at": event.get("available_at"),
                "signal_type": signal_type,
                "time_since_available_seconds": event.get("update_age_seconds"),
                "expected_locked_side_if_crossed": side_to_buy,
                "side_to_buy": side_to_buy,
                "best_ask": best_ask,
                "ask_depth": ask_depth,
                "spread": spread,
                "q_effective": best_ask,
                "price_bucket": price_bucket,
                "fair_probability": fair_probability,
                "latency_edge_estimate": latency_edge,
                "decision": decision,
                "blockers": sorted(set(blockers)),
                "anomaly_flags": anomaly_flags,
                "settlement_spec": _spec(row) or None,
                "market_side_pair_complete": pair.get("pair_complete"),
                "no_lookahead": True,
            }
        )
    return _summary(signal_rows, generated_at=generated_at, event_count=len(event_cache))


def _count(rows: Iterable[Dict[str, Any]], key: str, value: str) -> int:
    return len([row for row in rows if row.get(key) == value])


def _counter_rows(rows: Iterable[Dict[str, Any]], field: str, values_field: str) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        values = row.get(values_field) if isinstance(row.get(values_field), list) else [row.get(values_field)]
        for value in values:
            if value:
                counts[str(value)] = counts.get(str(value), 0) + 1
    return [
        {field: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _summary(signal_rows: List[Dict[str, Any]], *, generated_at: str, event_count: int) -> Dict[str, Any]:
    candidates = [row for row in signal_rows if row.get("decision") == "candidate"]
    watch = [row for row in signal_rows if row.get("decision") == "watch"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": {
            "scanned_row_count": len(signal_rows),
            "metar_update_event_count": event_count,
            "near_cross_count": _count(signal_rows, "signal_type", "ge_near_cross")
            + _count(signal_rows, "signal_type", "le_near_break"),
            "just_crossed_count": _count(signal_rows, "signal_type", "ge_just_crossed")
            + _count(signal_rows, "signal_type", "le_just_broken"),
            "candidate_count": len(candidates),
            "watch_count": len(watch),
            "reject_count": _count(signal_rows, "decision", "reject"),
            "reject_reason_counts": _counter_rows(signal_rows, "reason", "blockers"),
            "candidate_samples": candidates[:10],
            "top_watch_samples": watch[:10],
        },
        "rows": signal_rows,
    }
