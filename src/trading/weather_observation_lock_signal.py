from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.weather.weather_observations import (
    detect_station_observation_anomalies,
    observation_temperature_value,
)
from src.weather.weather_sources import snapshots_available_for_replay


SCHEMA_VERSION = "polyweather_weather_observation_lock_signal.v1"
DUST_PRICE_BUCKET = "price_lt_0_005"
SUPPORTED_LOCK_SOURCES = {"metar"}


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
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


def _bucket(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}


def _first_float(row: Dict[str, Any], fields: Iterable[str]) -> Optional[float]:
    spec = _spec(row)
    bucket = _bucket(row)
    for field in fields:
        for source in (row, spec, bucket):
            value = _safe_float(source.get(field))
            if value is not None:
                return value
    return None


def _first_text(row: Dict[str, Any], fields: Iterable[str]) -> Optional[str]:
    spec = _spec(row)
    bucket = _bucket(row)
    for field in fields:
        for source in (row, spec, bucket):
            text = _text(source.get(field))
            if text:
                return text
    return None


def _order_book(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("order_book") if isinstance(row.get("order_book"), dict) else row


def _book_float(row: Dict[str, Any], field: str) -> Optional[float]:
    value = _safe_float(row.get(field))
    if value is not None:
        return value
    return _safe_float(_order_book(row).get(field))


def _latest_observation(
    observations: Iterable[Dict[str, Any]],
    *,
    station_code: str,
    target_date: str,
    settlement_source: str,
    replay_time: Optional[str] = None,
) -> Dict[str, Any]:
    source = settlement_source.lower()
    scoped = [
        row
        for row in observations
        if _text(row.get("station_code")).upper() == station_code.upper()
        and _text(row.get("target_date")) == target_date
        and _text(row.get("source")).lower() == source
        and _text(row.get("snapshot_type") or "observation") == "observation"
    ]
    if replay_time:
        scoped = snapshots_available_for_replay(scoped, replay_time=replay_time)
    latest_at = None
    current_high: Optional[float] = None
    for row in scoped:
        observed_at = _text(row.get("observed_at") or row.get("available_at")) or None
        latest_at = max([value for value in (latest_at, observed_at) if value], default=None)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        explicit_high = _safe_float(
            payload.get("official_current_high")
            or payload.get("current_high")
            or payload.get("max_temp_c")
        )
        temperature = explicit_high if explicit_high is not None else observation_temperature_value(row)
        if temperature is not None:
            current_high = temperature if current_high is None else max(current_high, temperature)
    return {
        "rows": scoped,
        "latest_observation_at": latest_at,
        "official_current_high": current_high,
        "anomaly_flags": detect_station_observation_anomalies(scoped),
    }


def _lock_state(
    *,
    bucket_type: str,
    threshold: Optional[float],
    upper_threshold: Optional[float],
    official_current_high: Optional[float],
) -> Tuple[str, Optional[str], Optional[float]]:
    if official_current_high is None or threshold is None:
        return "missing_intraday_observation", None, None
    if bucket_type == "ge":
        if official_current_high >= threshold:
            return "ge_yes_locked", "YES", 1.0
        return "ge_not_locked", None, None
    if bucket_type == "le":
        if official_current_high > threshold:
            return "le_yes_dead_no_locked", "NO", 1.0
        return "le_not_locked", None, None
    if bucket_type == "range":
        if upper_threshold is not None and official_current_high > upper_threshold:
            return "range_yes_dead_no_locked", "NO", 1.0
        return "range_not_locked", None, None
    if bucket_type == "eq":
        if official_current_high > threshold:
            return "eq_yes_dead_no_locked", "NO", 1.0
        return "eq_not_locked", None, None
    return "unsupported_bucket", None, None


def _execution_price(row: Dict[str, Any], *, locked_side: Optional[str]) -> Dict[str, Any]:
    best_bid = _book_float(row, "best_bid")
    best_ask = _book_float(row, "best_ask")
    spread = _book_float(row, "spread")
    bid_depth = _book_float(row, "bid_depth_usdc_3c")
    ask_depth = _book_float(row, "ask_depth_usdc_3c")
    token_side = _text(row.get("side") or row.get("outcome")).upper()
    if locked_side == "NO" and token_side != "NO":
        q_effective = 1.0 - best_bid if best_bid is not None else None
        return {
            "q_effective": q_effective,
            "ask_depth": bid_depth,
            "price_semantics": "synthetic_no_from_yes_bid",
        }
    return {
        "q_effective": best_ask,
        "ask_depth": ask_depth,
        "price_semantics": "direct_token_best_ask",
    }


def build_observation_lock_signal_row(
    row: Dict[str, Any],
    *,
    observations: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
    min_executable_edge: float = 0.0,
    max_spread: float = 0.03,
    min_ask_depth: float = 1.0,
    default_cost: float = 0.005,
) -> Dict[str, Any]:
    bucket_type = _text(_first_text(row, ("bucket_type",)) or "").lower()
    threshold = _first_float(row, ("threshold",))
    upper_threshold = _first_float(row, ("upper_threshold",))
    station_code = _text(_first_text(row, ("station_code", "settlement_station_code")) or "").upper()
    settlement_source = _text(_first_text(row, ("settlement_source",)) or "").lower()
    target_date = _text(_first_text(row, ("target_date",)) or "")
    obs = _latest_observation(
        observations,
        station_code=station_code,
        target_date=target_date,
        settlement_source=settlement_source,
        replay_time=generated_at,
    )
    lock_state, locked_side, locked_probability = _lock_state(
        bucket_type=bucket_type,
        threshold=threshold,
        upper_threshold=upper_threshold,
        official_current_high=obs["official_current_high"],
    )
    execution = _execution_price(row, locked_side=locked_side)
    q_effective = execution["q_effective"]
    executable_edge = (
        round(1.0 - float(q_effective) - float(default_cost), 6)
        if locked_side and q_effective is not None
        else None
    )
    best_bid = _book_float(row, "best_bid")
    best_ask = _book_float(row, "best_ask")
    spread = _book_float(row, "spread")
    ask_depth = execution["ask_depth"]
    price_bucket = _price_bucket(q_effective)
    anomaly_flags = list(obs["anomaly_flags"])
    blockers: List[str] = []
    if lock_state in {"ge_not_locked", "le_not_locked", "range_not_locked", "eq_not_locked", "missing_intraday_observation"}:
        blockers.append("observation_not_locked")
    if lock_state == "unsupported_bucket":
        blockers.append("unsupported_bucket")
    if settlement_source not in SUPPORTED_LOCK_SOURCES:
        blockers.append("unsupported_official_source_not_alpha")
    if q_effective is None:
        blockers.append("missing_executable_price")
    if executable_edge is None or executable_edge <= float(min_executable_edge):
        blockers.append("executable_edge_not_positive")
    if spread is None:
        blockers.append("missing_spread")
    elif spread > float(max_spread):
        blockers.append("spread_too_wide")
    if ask_depth is None:
        blockers.append("missing_ask_depth")
    elif ask_depth < float(min_ask_depth):
        blockers.append("ask_depth_too_low")
    if anomaly_flags:
        blockers.append("blocked_by_observation_anomaly")
    if price_bucket == DUST_PRICE_BUCKET:
        blockers.append("dust_price_bucket_not_alpha")
    if bucket_type == "eq":
        blockers.append("eq_exact_not_alpha")

    if bucket_type == "eq" or price_bucket == DUST_PRICE_BUCKET:
        decision = "shadow"
    elif not locked_side or lock_state in {"ge_not_locked", "le_not_locked", "range_not_locked", "eq_not_locked", "missing_intraday_observation", "unsupported_bucket"}:
        decision = "reject"
    elif anomaly_flags:
        decision = "watch"
    elif blockers:
        decision = "watch"
    else:
        decision = "candidate"

    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "side": locked_side or _text(row.get("side") or row.get("outcome")).upper() or None,
        "token_side": _text(row.get("side") or row.get("outcome")).upper() or None,
        "price_semantics": execution["price_semantics"],
        "bucket_type": bucket_type,
        "threshold": threshold,
        "upper_threshold": upper_threshold,
        "station_code": station_code or None,
        "settlement_source": settlement_source or None,
        "target_date": target_date or None,
        "market_close_time": _first_text(row, ("market_close_time", "end_time", "end_date")),
        "observation_window_end_time": _first_text(row, ("observation_window_end_time",)),
        "latest_observation_at": obs["latest_observation_at"],
        "official_current_high": obs["official_current_high"],
        "lock_state": lock_state,
        "locked_side": locked_side,
        "locked_probability": locked_probability,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "ask_depth": ask_depth,
        "q_effective": q_effective,
        "executable_edge": executable_edge,
        "price_bucket": price_bucket,
        "anomaly_flags": anomaly_flags,
        "decision": decision,
        "blockers": sorted(set(blockers)),
    }


def build_observation_lock_signal_report(
    rows: Iterable[Dict[str, Any]],
    *,
    observations: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
    min_executable_edge: float = 0.0,
    max_spread: float = 0.03,
    min_ask_depth: float = 1.0,
) -> Dict[str, Any]:
    generated_at = generated_at or _now_iso()
    signal_rows = [
        build_observation_lock_signal_row(
            row,
            observations=observations,
            generated_at=generated_at,
            min_executable_edge=min_executable_edge,
            max_spread=max_spread,
            min_ask_depth=min_ask_depth,
        )
        for row in rows
        if isinstance(row, dict)
    ]
    decisions: Dict[str, int] = {}
    locks: Dict[str, int] = {}
    for row in signal_rows:
        decisions[str(row.get("decision") or "unknown")] = decisions.get(str(row.get("decision") or "unknown"), 0) + 1
        locks[str(row.get("lock_state") or "unknown")] = locks.get(str(row.get("lock_state") or "unknown"), 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": {
            "row_count": len(signal_rows),
            "candidate_count": decisions.get("candidate", 0),
            "watch_count": decisions.get("watch", 0),
            "shadow_count": decisions.get("shadow", 0),
            "reject_count": decisions.get("reject", 0),
            "decision_counts": [
                {"decision": key, "count": value}
                for key, value in sorted(decisions.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "lock_state_counts": [
                {"lock_state": key, "count": value}
                for key, value in sorted(locks.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
        },
        "rows": signal_rows,
    }
