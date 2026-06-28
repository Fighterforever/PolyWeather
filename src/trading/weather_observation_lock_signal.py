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


def _book_ladder(row: Dict[str, Any], side: str) -> List[Dict[str, float]]:
    book = _order_book(row)
    raw = book.get("asks" if side == "ask" else "bids")
    if raw is None:
        raw = book.get("ask_ladder" if side == "ask" else "bid_ladder")
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
    return sorted(rows, key=lambda item: item["price"], reverse=(side == "bid"))


def _market_key(row: Dict[str, Any]) -> str:
    return _text(row.get("market_slug") or row.get("market_id"))


def _token_side(row: Dict[str, Any]) -> str:
    return _text(row.get("side") or row.get("outcome")).upper()


def build_market_side_book_pair_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
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
            "ask_ladder": _book_ladder(row, "ask"),
            "bid_ladder": _book_ladder(row, "bid"),
        }
        pair = index.setdefault(
            key,
            {
                "market_slug": row.get("market_slug"),
                "market_id": row.get("market_id"),
                "YES": None,
                "NO": None,
            },
        )
        pair["market_slug"] = pair.get("market_slug") or row.get("market_slug")
        pair["market_id"] = pair.get("market_id") or row.get("market_id")
        pair[side] = entry
    for pair in index.values():
        yes = pair.get("YES") if isinstance(pair.get("YES"), dict) else {}
        no = pair.get("NO") if isinstance(pair.get("NO"), dict) else {}
        pair_complete = bool(yes and no)
        pair.update(
            {
                "yes_token_id": yes.get("token_id"),
                "no_token_id": no.get("token_id"),
                "yes_best_bid": yes.get("best_bid"),
                "yes_best_ask": yes.get("best_ask"),
                "yes_bid_depth": yes.get("bid_depth"),
                "yes_ask_depth": yes.get("ask_depth"),
                "no_best_bid": no.get("best_bid"),
                "no_best_ask": no.get("best_ask"),
                "no_bid_depth": no.get("bid_depth"),
                "no_ask_depth": no.get("ask_depth"),
                "yes_bid_ladder": yes.get("bid_ladder") or [],
                "yes_ask_ladder": yes.get("ask_ladder") or [],
                "no_bid_ladder": no.get("bid_ladder") or [],
                "no_ask_ladder": no.get("ask_ladder") or [],
                "pair_complete": pair_complete,
                "pair_gap_reason": None if pair_complete else "missing_counterpart_token_book",
            }
        )
    return index


def build_market_side_orderbook_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return build_market_side_book_pair_index(rows)


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
        "locked_side_best_bid": best_bid if token_side == locked_side else None,
        "locked_side_best_ask": best_ask if token_side == locked_side else None,
        "locked_side_bid_depth": bid_depth if token_side == locked_side else None,
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
            "ask_ladder": _book_ladder(row, "ask"),
            "bid_ladder": _book_ladder(row, "bid"),
        }
    if direct:
        direct_best_bid = _safe_float(direct.get("best_bid"))
        direct_best_ask = _safe_float(direct.get("best_ask"))
        direct_bid_depth = _safe_float(direct.get("bid_depth"))
        direct_ask_depth = _safe_float(direct.get("ask_depth"))
        direct_token_id = _text(direct.get("token_id"))
        return {
            "q_effective": direct_best_ask,
            "ask_depth": direct_ask_depth,
            "price_semantics": "direct_locked_side_best_ask",
            "locked_side_token_id": direct_token_id or None,
            "locked_side_token_available": True,
            "locked_side_best_bid": direct_best_bid,
            "locked_side_best_ask": direct_best_ask,
            "locked_side_bid_depth": direct_bid_depth,
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
            "locked_side_best_bid": None,
            "locked_side_best_ask": None,
            "locked_side_bid_depth": None,
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
        "locked_side_best_bid": None,
        "locked_side_best_ask": None,
        "locked_side_bid_depth": None,
        "locked_side_ask_depth": None,
        "executable_price_source": "missing_locked_side_book",
        "executable_price_source_type": "missing",
        "current_row_is_locked_side_token": False,
    }


def _dead_side_capture_fields(
    *,
    row: Dict[str, Any],
    locked_side: Optional[str],
    bucket_type: str,
    settlement_source: str,
    lock_is_immutable: bool,
    freshness: Dict[str, Any],
    anomaly_flags: List[str],
    market_side_index: Optional[Dict[str, Dict[str, Any]]],
    default_cost: float,
    min_dead_side_bid: float,
    min_dead_side_bid_depth: float,
) -> Dict[str, Any]:
    dead_side = "NO" if locked_side == "YES" else "YES" if locked_side == "NO" else None
    key = _market_key(row)
    market_entry = (market_side_index or {}).get(key) or {}
    dead_entry = market_entry.get(dead_side) if dead_side else None
    if not dead_entry and dead_side and _token_side(row) == dead_side:
        dead_entry = {
            "row": row,
            "token_id": _text(row.get("token_id") or _order_book(row).get("token_id")) or None,
            "best_bid": _book_float(row, "best_bid"),
            "bid_depth": _book_depth(row, "bid"),
        }
    dead_bid = _safe_float((dead_entry or {}).get("best_bid"))
    dead_depth = _safe_float((dead_entry or {}).get("bid_depth"))
    edge = round(float(dead_bid) - float(default_cost), 6) if dead_bid is not None else None
    blockers: List[str] = []
    if not dead_side:
        blockers.append("missing_locked_side")
    if bucket_type not in {"ge", "le"}:
        blockers.append("bucket_type_not_alpha")
    if settlement_source not in SUPPORTED_LOCK_SOURCES:
        blockers.append("unsupported_official_source_not_alpha")
    if not lock_is_immutable:
        blockers.append("lock_not_immutable")
    if freshness.get("freshness_blocker"):
        blockers.append(str(freshness["freshness_blocker"]))
    if anomaly_flags:
        blockers.append("blocked_by_observation_anomaly")
    if dead_bid is None or dead_bid <= 0:
        blockers.append("dead_side_bid_missing_or_zero")
    elif dead_bid <= float(min_dead_side_bid):
        blockers.append("dead_side_bid_below_min")
    if edge is None or edge <= 0:
        blockers.append("dead_side_capture_edge_not_positive")
    if dead_depth is None:
        blockers.append("dead_side_bid_depth_missing")
    elif dead_depth < float(min_dead_side_bid_depth):
        blockers.append("dead_side_bid_depth_too_low")
    if _price_bucket(dead_bid) == DUST_PRICE_BUCKET:
        blockers.append("dead_side_dust_bid_not_alpha")
    candidate = not blockers
    return {
        "dead_side": dead_side,
        "dead_side_token_id": (dead_entry or {}).get("token_id") if dead_entry else None,
        "dead_side_best_bid": dead_bid,
        "dead_side_bid_depth": dead_depth,
        "dead_side_capture_edge": edge,
        "dead_side_capture_candidate": bool(candidate),
        "dead_side_capture_blockers": sorted(set(blockers)),
        "dead_side_price_bucket": _price_bucket(dead_bid),
        "dead_side_capture_diagnostic_only": True,
        "dead_side_capture_counts_for_live_gate": False,
        "dead_side_capture_live_gate_excluded": True,
    }


def _market_reflection_state(
    *,
    locked_side: Optional[str],
    bucket_type: str,
    execution: Dict[str, Any],
    executable_edge: Optional[float],
    dead_side: Dict[str, Any],
    market_pair: Dict[str, Any],
    min_executable_edge: float,
    min_dead_side_bid: float,
) -> str:
    if locked_side not in {"YES", "NO"} or bucket_type not in {"ge", "le", "eq"}:
        return "not_locked"
    if (
        execution.get("executable_price_source_type") == "direct_locked_side_book"
        and execution.get("q_effective") is not None
        and executable_edge is not None
        and executable_edge > float(min_executable_edge)
    ):
        return "locked_but_executable_edge_available"
    if execution.get("executable_price_source_type") == "synthetic_from_opposite_bid":
        return "locked_but_only_synthetic_diagnostic"
    locked_bid = _safe_float(execution.get("locked_side_best_bid"))
    locked_ask = _safe_float(execution.get("locked_side_best_ask"))
    dead_bid = _safe_float(dead_side.get("dead_side_best_bid"))
    if (
        locked_bid is not None
        and locked_bid >= 0.98
        and locked_ask is None
        and (dead_bid is None or dead_bid <= float(min_dead_side_bid))
    ):
        return "market_already_reflected_lock"
    if not market_pair.get("pair_complete"):
        return "locked_but_missing_pair_book"
    return "locked_but_no_current_edge"


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
    min_dead_side_bid: float = 0.005,
    min_dead_side_bid_depth: float = 1.0,
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
    exact_dead_no_lock = bucket_type == "eq" and lock_state == "eq_yes_dead_no_locked" and locked_side == "NO"
    eq_yes_prediction_forbidden = bucket_type == "eq" and not exact_dead_no_lock
    strategy_id = "eq_dead_no_lock" if exact_dead_no_lock else "observation_lock"
    lock_is_immutable = lock_state in {"ge_yes_locked", "le_yes_dead_no_locked", "eq_yes_dead_no_locked"}
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
    market_pair = (market_side_index or {}).get(_market_key(row)) or {}
    anomaly_flags = list(obs["anomaly_flags"])
    dead_side = _dead_side_capture_fields(
        row=row,
        locked_side=locked_side,
        bucket_type=bucket_type,
        settlement_source=settlement_source,
        lock_is_immutable=lock_is_immutable,
        freshness=freshness,
        anomaly_flags=anomaly_flags,
        market_side_index=market_side_index,
        default_cost=default_cost,
        min_dead_side_bid=min_dead_side_bid,
        min_dead_side_bid_depth=min_dead_side_bid_depth,
    )
    market_reflection_state = _market_reflection_state(
        locked_side=locked_side,
        bucket_type=bucket_type,
        execution={**execution, "q_effective": q_effective},
        executable_edge=executable_edge,
        dead_side=dead_side,
        market_pair=market_pair,
        min_executable_edge=min_executable_edge,
        min_dead_side_bid=min_dead_side_bid,
    )
    execution_mode = None
    execution_mode_status = None
    if market_reflection_state == "locked_but_executable_edge_available":
        execution_mode = "direct_locked_side_taker"
        execution_mode_status = "paper_only_direct_locked_side_taker"
    elif dead_side.get("dead_side_capture_candidate"):
        execution_mode = "dead_side_bid_capture"
        execution_mode_status = "diagnostic_only_until_ctf_split_merge_supported"
    best_bid = _book_float(row, "best_bid")
    best_ask = _book_float(row, "best_ask")
    spread = _book_float(row, "spread")
    ask_depth = execution["ask_depth"]
    price_bucket = _price_bucket(q_effective)
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
    if bucket_type == "eq" and not exact_dead_no_lock:
        blockers.append("exact_yes_prediction_unstable")
    if exact_dead_no_lock and execution.get("executable_price_source_type") != "direct_locked_side_book":
        blockers.append("eq_dead_no_requires_direct_no_ask")

    if bucket_type == "eq" and not exact_dead_no_lock:
        decision = "shadow"
    elif price_bucket == DUST_PRICE_BUCKET:
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
        "strategy_id": strategy_id,
        "eq_yes_prediction_forbidden": bool(eq_yes_prediction_forbidden),
        "exact_dead_no_lock_candidate": bool(exact_dead_no_lock and decision == "candidate"),
        "live_gate_excluded_reason": "exact_dead_no_needs_forward_evidence" if exact_dead_no_lock else None,
        "eq_yes_prediction_status": "forbidden_live" if eq_yes_prediction_forbidden else None,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id") or row.get("snapshot_id"),
        "side": locked_side or _text(row.get("side") or row.get("outcome")).upper() or None,
        "token_side": _text(row.get("side") or row.get("outcome")).upper() or None,
        "price_semantics": execution["price_semantics"],
        "market_side_pair_complete": market_pair.get("pair_complete"),
        "market_side_pair_gap_reason": market_pair.get("pair_gap_reason"),
        "locked_side_token_id": execution.get("locked_side_token_id"),
        "locked_side_token_available": execution.get("locked_side_token_available"),
        "locked_side_best_bid": execution.get("locked_side_best_bid"),
        "locked_side_best_ask": execution.get("locked_side_best_ask"),
        "locked_side_bid_depth": execution.get("locked_side_bid_depth"),
        "locked_side_ask_depth": execution.get("locked_side_ask_depth"),
        "executable_price_source": execution.get("executable_price_source"),
        "executable_price_source_type": execution.get("executable_price_source_type"),
        "synthetic_price_diagnostic_only": execution.get("synthetic_price_diagnostic_only"),
        "current_row_is_locked_side_token": execution.get("current_row_is_locked_side_token"),
        **dead_side,
        "execution_mode": execution_mode,
        "execution_mode_status": execution_mode_status,
        "market_reflection_state": market_reflection_state,
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
    default_cost: float = 0.005,
    min_dead_side_bid: float = 0.005,
    min_dead_side_bid_depth: float = 1.0,
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
            default_cost=default_cost,
            min_dead_side_bid=min_dead_side_bid,
            min_dead_side_bid_depth=min_dead_side_bid_depth,
            market_side_index=market_side_index,
        )
        for row in source_rows
    ]
    decisions: Dict[str, int] = {}
    locks: Dict[str, int] = {}
    stations: Dict[str, int] = {}
    buckets: Dict[str, int] = {}
    blockers: Dict[str, int] = {}
    reflection_states: Dict[str, int] = {}
    execution_modes: Dict[str, int] = {}
    for row in signal_rows:
        decisions[str(row.get("decision") or "unknown")] = decisions.get(str(row.get("decision") or "unknown"), 0) + 1
        locks[str(row.get("lock_state") or "unknown")] = locks.get(str(row.get("lock_state") or "unknown"), 0) + 1
        stations[str(row.get("station_code") or "unknown")] = stations.get(str(row.get("station_code") or "unknown"), 0) + 1
        buckets[str(row.get("bucket_type") or "unknown")] = buckets.get(str(row.get("bucket_type") or "unknown"), 0) + 1
        reflection_states[str(row.get("market_reflection_state") or "unknown")] = (
            reflection_states.get(str(row.get("market_reflection_state") or "unknown"), 0) + 1
        )
        execution_modes[str(row.get("execution_mode") or "none")] = (
            execution_modes.get(str(row.get("execution_mode") or "none"), 0) + 1
        )
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
    direct_locked_side_taker_candidate_count = len(
        [
            row
            for row in signal_rows
            if row.get("execution_mode") == "direct_locked_side_taker"
            and row.get("decision") == "candidate"
        ]
    )
    direct_locked_side_missing_ask_count = len(
        [
            row
            for row in signal_rows
            if row.get("lock_state") in {"ge_yes_locked", "le_yes_dead_no_locked"}
            and row.get("bucket_type") in {"ge", "le"}
            and row.get("executable_price_source_type") == "direct_locked_side_book"
            and row.get("locked_side_best_ask") is None
        ]
    )
    dead_side_bid_available_count = len(
        [
            row
            for row in signal_rows
            if row.get("lock_state") in {"ge_yes_locked", "le_yes_dead_no_locked"}
            and row.get("bucket_type") in {"ge", "le"}
            and _safe_float(row.get("dead_side_best_bid")) is not None
            and float(row.get("dead_side_best_bid") or 0.0) > 0
        ]
    )
    dead_side_capture_candidate_count = len([row for row in signal_rows if row.get("dead_side_capture_candidate") is True])
    dead_side_capture_positive_edge_count = len(
        [
            row
            for row in signal_rows
            if _safe_float(row.get("dead_side_capture_edge")) is not None
            and float(row.get("dead_side_capture_edge") or 0.0) > 0
        ]
    )
    market_already_reflected_lock_count = len(
        [row for row in signal_rows if row.get("market_reflection_state") == "market_already_reflected_lock"]
    )
    eq_dead_no_lock_count = len([row for row in signal_rows if row.get("strategy_id") == "eq_dead_no_lock"])
    eq_dead_no_candidate_count = len(
        [
            row
            for row in signal_rows
            if row.get("strategy_id") == "eq_dead_no_lock" and row.get("decision") == "candidate"
        ]
    )
    eq_yes_prediction_forbidden_count = len([row for row in signal_rows if row.get("eq_yes_prediction_forbidden")])
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
            "direct_locked_side_taker_candidate_count": direct_locked_side_taker_candidate_count,
            "direct_locked_side_missing_ask_count": direct_locked_side_missing_ask_count,
            "dead_side_bid_available_count": dead_side_bid_available_count,
            "dead_side_capture_candidate_count": dead_side_capture_candidate_count,
            "dead_side_capture_positive_edge_count": dead_side_capture_positive_edge_count,
            "market_already_reflected_lock_count": market_already_reflected_lock_count,
            "eq_dead_no_lock_count": eq_dead_no_lock_count,
            "eq_dead_no_candidate_count": eq_dead_no_candidate_count,
            "eq_yes_prediction_forbidden_count": eq_yes_prediction_forbidden_count,
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
            "market_reflection_state_counts": [
                {"market_reflection_state": key, "count": value}
                for key, value in sorted(reflection_states.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "execution_mode_counts": [
                {"execution_mode": key, "count": value}
                for key, value in sorted(execution_modes.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "candidate_samples": [
                row
                for row in signal_rows
                if row.get("decision") == "candidate"
            ][:10],
        },
        "rows": signal_rows,
    }
