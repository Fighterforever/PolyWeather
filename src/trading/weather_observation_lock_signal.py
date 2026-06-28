from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.weather.weather_observations import (
    OfficialIntradayObservationRepository,
    detect_station_observation_anomalies,
    observation_temperature_value,
)
from src.weather.weather_sources import parse_utc, snapshots_available_for_replay


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


def _book_depth(row: Dict[str, Any], side: str) -> Optional[float]:
    fields = (
        ("ask_depth_usdc_3c", "ask_depth", "asks_depth")
        if side == "ask"
        else ("bid_depth_usdc_3c", "bid_depth", "bids_depth")
    )
    for field in fields:
        value = _book_float(row, field)
        if value is not None:
            return value
    ladder = _order_book(row).get("asks" if side == "ask" else "bids")
    if not isinstance(ladder, list):
        ladder = _order_book(row).get("ask_ladder" if side == "ask" else "bid_ladder")
    total = 0.0
    found = False
    if isinstance(ladder, list):
        for level in ladder:
            if not isinstance(level, dict):
                continue
            size = _safe_float(level.get("size"))
            if size is not None:
                total += float(size)
                found = True
    return round(total, 8) if found else None


def _market_key(row: Dict[str, Any]) -> str:
    return _text(row.get("market_slug") or row.get("market_id"))


def _token_side(row: Dict[str, Any]) -> str:
    return _text(row.get("side") or row.get("outcome")).upper()


def build_market_side_orderbook_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _market_key(row)
        side = _token_side(row)
        if not key or side not in {"YES", "NO"}:
            continue
        book = _order_book(row)
        token_id = _text(row.get("token_id") or book.get("token_id"))
        entry = {
            "row": row,
            "token_id": token_id or None,
            "best_bid": _book_float(row, "best_bid"),
            "best_ask": _book_float(row, "best_ask"),
            "spread": _book_float(row, "spread"),
            "ask_depth": _book_depth(row, "ask"),
            "bid_depth": _book_depth(row, "bid"),
        }
        index.setdefault(key, {})[side] = entry
    return index


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
        and _text(row.get("target_date") or row.get("target_date_local")) == target_date
        and (
            _text(row.get("settlement_source") or row.get("source")).lower() == source
            or (source == "metar" and _text(row.get("source")).lower().startswith("aviationweather_metar"))
        )
        and _text(row.get("snapshot_type") or "observation") == "observation"
    ]
    raw_scoped_count = len(scoped)
    wrong_target_date_count = 0
    future_available_count = 0
    if replay_time:
        replay_dt = parse_utc(replay_time)
        station_scoped = [
            row
            for row in observations
            if _text(row.get("station_code")).upper() == station_code.upper()
            and (
                _text(row.get("settlement_source") or row.get("source")).lower() == source
                or (source == "metar" and _text(row.get("source")).lower().startswith("aviationweather_metar"))
            )
            and _text(row.get("snapshot_type") or "observation") == "observation"
        ]
        wrong_target_date_count = len(
            [
                row
                for row in station_scoped
                if _text(row.get("target_date") or row.get("target_date_local")) != target_date
            ]
        )
        if replay_dt is not None:
            future_available_count = len(
                [
                    row
                    for row in scoped
                    if (parse_utc(row.get("available_at")) is not None and parse_utc(row.get("available_at")) > replay_dt)
                ]
            )
    if replay_time:
        scoped = snapshots_available_for_replay(scoped, replay_time=replay_time)
    latest_at = None
    latest_available_at = None
    current_high: Optional[float] = None
    for row in scoped:
        observed_at = _text(row.get("observed_at") or row.get("available_at")) or None
        available_at = _text(row.get("available_at")) or None
        latest_at = max([value for value in (latest_at, observed_at) if value], default=None)
        latest_available_at = max([value for value in (latest_available_at, available_at) if value], default=None)
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
        "latest_available_at": latest_available_at,
        "official_current_high": current_high,
        "anomaly_flags": detect_station_observation_anomalies(scoped),
        "observation_count": len(scoped),
        "raw_scoped_count": raw_scoped_count,
        "wrong_target_date_count": wrong_target_date_count,
        "future_available_count": future_available_count,
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


def _execution_price(
    row: Dict[str, Any],
    *,
    locked_side: Optional[str],
    market_side_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    best_bid = _book_float(row, "best_bid")
    best_ask = _book_float(row, "best_ask")
    spread = _book_float(row, "spread")
    bid_depth = _book_depth(row, "bid")
    ask_depth = _book_depth(row, "ask")
    token_side = _token_side(row)
    empty = {
        "q_effective": best_ask,
        "ask_depth": ask_depth,
        "price_semantics": "direct_token_best_ask",
        "locked_side_token_id": row.get("token_id"),
        "locked_side_token_available": token_side in {"YES", "NO"} and token_side == locked_side,
        "locked_side_best_ask": best_ask if token_side == locked_side else None,
        "locked_side_ask_depth": ask_depth if token_side == locked_side else None,
        "executable_price_source": "row_token_book",
        "executable_price_source_type": "direct_locked_side_book" if token_side == locked_side else "missing",
        "synthetic_price_diagnostic_only": False,
        "current_row_is_locked_side_token": token_side == locked_side,
    }
    if locked_side not in {"YES", "NO"}:
        return empty
    key = _market_key(row)
    market_entry = (market_side_index or {}).get(key) or {}
    direct = market_entry.get(locked_side)
    opposite = market_entry.get("NO" if locked_side == "YES" else "YES")
    if not direct and token_side == locked_side:
        direct = {
            "row": row,
            "token_id": _text(row.get("token_id") or _order_book(row).get("token_id")) or None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "ask_depth": ask_depth,
            "bid_depth": bid_depth,
        }
    if direct:
        direct_best_ask = _safe_float(direct.get("best_ask"))
        direct_ask_depth = _safe_float(direct.get("ask_depth"))
        direct_token_id = _text(direct.get("token_id"))
        return {
            "q_effective": direct_best_ask,
            "ask_depth": direct_ask_depth,
            "price_semantics": "direct_locked_side_best_ask",
            "locked_side_token_id": direct_token_id or None,
            "locked_side_token_available": True,
            "locked_side_best_ask": direct_best_ask,
            "locked_side_ask_depth": direct_ask_depth,
            "executable_price_source": "direct_locked_side_book",
            "executable_price_source_type": "direct_locked_side_book",
            "synthetic_price_diagnostic_only": False,
            "current_row_is_locked_side_token": _text(row.get("token_id")) == direct_token_id,
        }
    if locked_side == "NO" and opposite:
        opposite_best_bid = _safe_float(opposite.get("best_bid"))
        q_effective = 1.0 - opposite_best_bid if opposite_best_bid is not None else None
        return {
            "q_effective": q_effective,
            "ask_depth": _safe_float(opposite.get("bid_depth")),
            "price_semantics": "synthetic_no_from_yes_bid",
            "locked_side_token_id": None,
            "locked_side_token_available": False,
            "locked_side_best_ask": None,
            "locked_side_ask_depth": None,
            "executable_price_source": "synthetic_from_opposite_bid",
            "executable_price_source_type": "synthetic_from_opposite_bid" if q_effective is not None else "missing",
            "synthetic_price_diagnostic_only": True,
            "current_row_is_locked_side_token": False,
        }
    return {
        **empty,
        "q_effective": None,
        "ask_depth": None,
        "locked_side_token_id": None,
        "locked_side_token_available": False,
        "locked_side_best_ask": None,
        "locked_side_ask_depth": None,
        "executable_price_source": "missing_locked_side_book",
        "executable_price_source_type": "missing",
        "current_row_is_locked_side_token": False,
    }


def _target_date_from_observation_at(value: Any) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        return ""
    return parsed.date().isoformat()


def _freshness_fields(
    *,
    obs: Dict[str, Any],
    generated_at: str,
    target_date: str,
    lock_state: str,
    lock_is_immutable: bool,
) -> Dict[str, Any]:
    latest_available = parse_utc(obs.get("latest_available_at"))
    generated = parse_utc(generated_at)
    latest_observation_date = _target_date_from_observation_at(obs.get("latest_observation_at"))
    target_valid = bool(latest_observation_date and latest_observation_date == target_date)
    if not target_valid and int(obs.get("observation_count") or 0) <= 0 and int(obs.get("wrong_target_date_count") or 0) <= 0:
        target_valid = False
    blocker: Optional[str] = None
    warning: Optional[str] = None
    status = "missing"
    if int(obs.get("future_available_count") or 0) > 0 or (latest_available is not None and generated is not None and latest_available > generated):
        blocker = "future_available_at"
        status = "future_blocker"
    elif int(obs.get("wrong_target_date_count") or 0) > 0 and int(obs.get("observation_count") or 0) <= 0:
        blocker = "wrong_observation_target_date"
        status = "wrong_target_date_blocker"
    elif not target_valid:
        blocker = "wrong_observation_target_date" if obs.get("latest_observation_at") else None
        status = "missing"
    elif latest_available is not None and generated is not None:
        is_stale = (generated - latest_available).total_seconds() > 10 * 60
        if is_stale and lock_is_immutable:
            warning = "stale_intraday_observation"
            status = "stale_warning"
        elif is_stale:
            blocker = "stale_intraday_observation"
            status = "stale_blocker"
        else:
            status = "fresh"
    return {
        "lock_is_immutable": bool(lock_is_immutable),
        "freshness_status": status,
        "freshness_warning": warning,
        "freshness_blocker": blocker,
        "observation_target_date_valid": target_valid,
    }


def build_observation_lock_signal_row(
    row: Dict[str, Any],
    *,
    observations: Iterable[Dict[str, Any]],
    intraday_repository: Optional[OfficialIntradayObservationRepository] = None,
    generated_at: Optional[str] = None,
    min_executable_edge: float = 0.0,
    max_spread: float = 0.03,
    min_ask_depth: float = 1.0,
    default_cost: float = 0.005,
    market_side_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    bucket_type = _text(_first_text(row, ("bucket_type",)) or "").lower()
    threshold = _first_float(row, ("threshold",))
    upper_threshold = _first_float(row, ("upper_threshold",))
    station_code = _text(_first_text(row, ("station_code", "settlement_station_code")) or "").upper()
    settlement_source = _text(_first_text(row, ("settlement_source",)) or "").lower()
    target_date = _text(_first_text(row, ("target_date",)) or "")
    if intraday_repository is not None and station_code and target_date:
        obs = intraday_repository.current_high_as_of(
            station_code=station_code,
            target_date=target_date,
            replay_time=generated_at or _now_iso(),
            settlement_source=settlement_source or "metar",
        )
        obs = {
            "rows": [],
            "latest_observation_at": obs.get("latest_observation_at"),
            "latest_available_at": obs.get("latest_available_at"),
            "official_current_high": obs.get("current_high_c"),
            "anomaly_flags": obs.get("anomaly_flags") or [],
            "quality_flags": obs.get("quality_flags") or [],
            "observation_count": obs.get("observation_count") or 0,
            "no_lookahead": obs.get("no_lookahead"),
            "raw_scoped_count": 0,
            "wrong_target_date_count": 0,
            "future_available_count": 0,
        }
    else:
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
    lock_is_immutable = lock_state in {"ge_yes_locked", "le_yes_dead_no_locked"}
    freshness = _freshness_fields(
        obs=obs,
        generated_at=generated_at or _now_iso(),
        target_date=target_date,
        lock_state=lock_state,
        lock_is_immutable=lock_is_immutable,
    )
    execution = _execution_price(row, locked_side=locked_side, market_side_index=market_side_index)
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
    if freshness.get("freshness_blocker"):
        blockers.append(str(freshness["freshness_blocker"]))
    if locked_side and execution.get("executable_price_source_type") == "missing":
        blockers.append("missing_locked_side_orderbook")
    if locked_side and execution.get("executable_price_source_type") == "synthetic_from_opposite_bid":
        blockers.append("synthetic_no_price_diagnostic_only")
    if locked_side and not execution.get("current_row_is_locked_side_token"):
        blockers.append("not_locked_side_token_row")
    if locked_side and q_effective is None:
        blockers.append("missing_executable_price")
    if locked_side and q_effective is not None and executable_edge is not None and executable_edge <= float(min_executable_edge):
        blockers.append("executable_edge_not_positive")
    if spread is None:
        blockers.append("missing_spread")
    elif spread > float(max_spread):
        blockers.append("spread_too_wide")
    if locked_side and ask_depth is None:
        blockers.append("missing_ask_depth")
    elif locked_side and ask_depth < float(min_ask_depth):
        blockers.append("ask_depth_too_low")
        blockers.append("locked_side_ask_depth_too_low")
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
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id") or row.get("snapshot_id"),
        "side": locked_side or _text(row.get("side") or row.get("outcome")).upper() or None,
        "token_side": _text(row.get("side") or row.get("outcome")).upper() or None,
        "price_semantics": execution["price_semantics"],
        "locked_side_token_id": execution.get("locked_side_token_id"),
        "locked_side_token_available": execution.get("locked_side_token_available"),
        "locked_side_best_ask": execution.get("locked_side_best_ask"),
        "locked_side_ask_depth": execution.get("locked_side_ask_depth"),
        "executable_price_source": execution.get("executable_price_source"),
        "executable_price_source_type": execution.get("executable_price_source_type"),
        "synthetic_price_diagnostic_only": execution.get("synthetic_price_diagnostic_only"),
        "current_row_is_locked_side_token": execution.get("current_row_is_locked_side_token"),
        "bucket_type": bucket_type,
        "threshold": threshold,
        "upper_threshold": upper_threshold,
        "station_code": station_code or None,
        "settlement_source": settlement_source or None,
        "target_date": target_date or None,
        "settlement_spec": _spec(row) or None,
        "market_close_time": _first_text(row, ("market_close_time", "end_time", "end_date")),
        "observation_window_end_time": _first_text(row, ("observation_window_end_time",)),
        "latest_observation_at": obs["latest_observation_at"],
        "latest_available_at": obs.get("latest_available_at"),
        "official_current_high": obs["official_current_high"],
        "intraday_observation_count": obs.get("observation_count") or 0,
        "no_lookahead": obs.get("no_lookahead", True),
        **freshness,
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
    intraday_repository: Optional[OfficialIntradayObservationRepository] = None,
    generated_at: Optional[str] = None,
    min_executable_edge: float = 0.0,
    max_spread: float = 0.03,
    min_ask_depth: float = 1.0,
) -> Dict[str, Any]:
    generated_at = generated_at or _now_iso()
    source_rows = [row for row in rows if isinstance(row, dict)]
    market_side_index = build_market_side_orderbook_index(source_rows)
    signal_rows = [
        build_observation_lock_signal_row(
            row,
            observations=observations,
            intraday_repository=intraday_repository,
            generated_at=generated_at,
            min_executable_edge=min_executable_edge,
            max_spread=max_spread,
            min_ask_depth=min_ask_depth,
            market_side_index=market_side_index,
        )
        for row in source_rows
    ]
    decisions: Dict[str, int] = {}
    locks: Dict[str, int] = {}
    stations: Dict[str, int] = {}
    buckets: Dict[str, int] = {}
    blockers: Dict[str, int] = {}
    for row in signal_rows:
        decisions[str(row.get("decision") or "unknown")] = decisions.get(str(row.get("decision") or "unknown"), 0) + 1
        locks[str(row.get("lock_state") or "unknown")] = locks.get(str(row.get("lock_state") or "unknown"), 0) + 1
        stations[str(row.get("station_code") or "unknown")] = stations.get(str(row.get("station_code") or "unknown"), 0) + 1
        buckets[str(row.get("bucket_type") or "unknown")] = buckets.get(str(row.get("bucket_type") or "unknown"), 0) + 1
        for blocker in row.get("blockers") or []:
            blockers[str(blocker)] = blockers.get(str(blocker), 0) + 1
    missing_intraday_count = len([row for row in signal_rows if row.get("lock_state") == "missing_intraday_observation"])
    locked_count = len([row for row in signal_rows if str(row.get("lock_state") or "").endswith("_locked")])
    ge_le_locked_count = len(
        [
            row
            for row in signal_rows
            if row.get("lock_state") in {"ge_yes_locked", "le_yes_dead_no_locked"}
            and row.get("bucket_type") in {"ge", "le"}
        ]
    )
    direct_locked_side_book_count = len(
        [row for row in signal_rows if row.get("executable_price_source_type") == "direct_locked_side_book"]
    )
    synthetic_locked_side_price_count = len(
        [row for row in signal_rows if row.get("executable_price_source_type") == "synthetic_from_opposite_bid"]
    )
    missing_locked_side_orderbook_count = len(
        [
            row
            for row in signal_rows
            if row.get("locked_side") and row.get("executable_price_source_type") == "missing"
        ]
    )
    stale_warning_count = len([row for row in signal_rows if row.get("freshness_warning") == "stale_intraday_observation"])
    stale_blocker_count = len([row for row in signal_rows if row.get("freshness_blocker") == "stale_intraday_observation"])
    visible_station_count = len(
        {
            str(row.get("station_code"))
            for row in signal_rows
            if int(row.get("intraday_observation_count") or 0) > 0 and str(row.get("station_code") or "")
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": {
            "total_rows": len(signal_rows),
            "row_count": len(signal_rows),
            "intraday_visible_station_count": visible_station_count,
            "missing_intraday_count": missing_intraday_count,
            "locked_count": locked_count,
            "ge_le_locked_count": ge_le_locked_count,
            "stale_warning_count": stale_warning_count,
            "stale_blocker_count": stale_blocker_count,
            "direct_locked_side_book_count": direct_locked_side_book_count,
            "synthetic_locked_side_price_count": synthetic_locked_side_price_count,
            "missing_locked_side_orderbook_count": missing_locked_side_orderbook_count,
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
            "by_station": [
                {"station_code": key, "count": value}
                for key, value in sorted(stations.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "by_bucket_type": [
                {"bucket_type": key, "count": value}
                for key, value in sorted(buckets.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "blocker_counts": [
                {"blocker": key, "count": value}
                for key, value in sorted(blockers.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "candidate_samples": [
                row
                for row in signal_rows
                if row.get("decision") == "candidate"
            ][:10],
        },
        "rows": signal_rows,
    }
