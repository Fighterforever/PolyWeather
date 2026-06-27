from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_market_catalog import (
    observation_window_end_time_utc,
    settlement_due_time_utc,
    settlement_grace_hours_for_source,
)
from src.trading.weather_paper_journal import _safe_float, load_jsonl, utc_now_iso


ORDERBOOK_ARCHIVE_COVERAGE_SCHEMA_VERSION = "polyweather_weather_orderbook_archive_coverage.v1"
ORDERBOOK_CLOSED_TOKEN_COVERAGE_SCHEMA_VERSION = "polyweather_weather_orderbook_closed_token_coverage.v1"
ORDERBOOK_CLOSED_BACKFILL_FOLLOWUP_SCHEMA_VERSION = "polyweather_weather_closed_backfill_followup_plan.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_utc(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _market_close_time(row: Dict[str, Any]) -> Optional[datetime]:
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    for value in (
        spec.get("market_close_time"),
        spec.get("end_time"),
        row.get("market_close_time"),
        row.get("end_date"),
        row.get("endDate"),
        row.get("end_time"),
        row.get("endTime"),
    ):
        parsed = _parse_utc(value)
        if parsed is not None:
            return parsed
    return None


def _timezone_name(row: Dict[str, Any]) -> Optional[str]:
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    return _text(row.get("settlement_timezone") or row.get("timezone") or spec.get("timezone")) or None


def _target_date(row: Dict[str, Any]) -> Optional[str]:
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    return _text(row.get("target_date") or spec.get("target_date")) or None


def _settlement_source(row: Dict[str, Any]) -> Optional[str]:
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    return _text(row.get("settlement_source") or spec.get("settlement_source")) or None


def _observation_window_end_time(row: Dict[str, Any]) -> Optional[datetime]:
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    for value in (row.get("observation_window_end_time"), spec.get("observation_window_end_time")):
        parsed = _parse_utc(value)
        if parsed is not None:
            return parsed
    derived = observation_window_end_time_utc(_target_date(row), _timezone_name(row))
    return _parse_utc(derived)


def _settlement_due_time(row: Dict[str, Any]) -> Optional[datetime]:
    spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
    for value in (row.get("settlement_due_time"), spec.get("settlement_due_time")):
        parsed = _parse_utc(value)
        if parsed is not None:
            return parsed
    derived = settlement_due_time_utc(_target_date(row), _timezone_name(row), _settlement_source(row))
    return _parse_utc(derived)


def _recorded_at(row: Dict[str, Any]) -> Optional[datetime]:
    for field in ("recorded_at", "available_at", "generated_at"):
        parsed = _parse_utc(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _side(row: Dict[str, Any]) -> str:
    return _text(row.get("side") or row.get("outcome")).lower()


def _orderbook_ready(row: Dict[str, Any]) -> bool:
    order_book = row.get("order_book") if isinstance(row.get("order_book"), dict) else {}
    asks = order_book.get("ask_ladder") or order_book.get("asks") or row.get("ask_ladder") or row.get("asks")
    best_ask = _safe_float(order_book.get("best_ask") or row.get("best_ask") or row.get("ask"))
    return isinstance(asks, list) and bool(asks) and best_ask is not None


def _archived_tokens(
    archive_rows: Iterable[Dict[str, Any]],
    *,
    now: datetime,
) -> Dict[str, List[datetime]]:
    tokens: Dict[str, List[datetime]] = {}
    for row in archive_rows:
        if not isinstance(row, dict):
            continue
        token_id = _text(row.get("token_id"))
        if not token_id:
            continue
        side = _side(row)
        if side not in {"", "yes", "y"}:
            continue
        recorded_at = _recorded_at(row)
        if recorded_at is None or recorded_at > now:
            continue
        if not _orderbook_ready(row):
            continue
        tokens.setdefault(token_id, []).append(recorded_at)
    return tokens


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        value = _text(record.get(field)) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _yes_token_id(record: Dict[str, Any]) -> Optional[str]:
    token_map = record.get("token_id_by_outcome") if isinstance(record.get("token_id_by_outcome"), dict) else {}
    token_id = _text(token_map.get("Yes") or token_map.get("yes"))
    if token_id:
        return token_id
    if _text(record.get("winning_outcome")).lower() == "yes":
        return _text(record.get("winning_token_id")) or None
    return None


def _closed_token_records(closed_records: Iterable[Dict[str, Any]]) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    tokens: Dict[str, Dict[str, Any]] = {}
    gaps: List[Dict[str, Any]] = []
    for record in closed_records:
        if not isinstance(record, dict) or record.get("status") != "resolved":
            continue
        token_id = _yes_token_id(record)
        base = {
            "market_id": record.get("market_id"),
            "market_slug": record.get("market_slug"),
            "city": record.get("city"),
            "bucket_label": record.get("bucket_label"),
            "target_date": record.get("target_date"),
            "end_time": _iso(_market_close_time(record)) if _market_close_time(record) else None,
            "market_close_time": _iso(_market_close_time(record)) if _market_close_time(record) else None,
            "observation_window_end_time": (
                _iso(_observation_window_end_time(record)) if _observation_window_end_time(record) else None
            ),
            "settlement_due_time": _iso(_settlement_due_time(record)) if _settlement_due_time(record) else None,
        }
        if not token_id:
            gaps.append({**base, "gap_reason": "closed_record_missing_yes_token_id"})
            continue
        tokens.setdefault(
            token_id,
            {
                **base,
                "token_id": token_id,
                "record_count": 0,
            },
        )
        tokens[token_id]["record_count"] += 1
    return tokens, gaps


def _archived_token_records(archive_rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    tokens: Dict[str, Dict[str, Any]] = {}
    for row in archive_rows:
        if not isinstance(row, dict):
            continue
        side = _side(row)
        if side not in {"", "yes", "y"}:
            continue
        token_id = _text(row.get("token_id"))
        if not token_id:
            continue
        recorded_at = _recorded_at(row)
        market_close_time = _market_close_time(row)
        observation_end_time = _observation_window_end_time(row)
        settlement_due_time = _settlement_due_time(row)
        settlement_spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else {}
        market_bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}
        settlement_source = _settlement_source(row)
        existing = tokens.setdefault(
            token_id,
            {
                "token_id": token_id,
                "market_id": row.get("market_id"),
                "market_slug": row.get("market_slug"),
                "city": row.get("city"),
                "bucket_label": row.get("bucket_label"),
                "bucket_type": row.get("bucket_type") or market_bucket.get("bucket_type"),
                "target_date": row.get("target_date") or settlement_spec.get("target_date"),
                "end_time": _iso(market_close_time) if market_close_time else None,
                "market_close_time": _iso(market_close_time) if market_close_time else None,
                "observation_window_end_time": _iso(observation_end_time) if observation_end_time else None,
                "settlement_due_time": _iso(settlement_due_time) if settlement_due_time else None,
                "settlement_grace_hours": row.get("settlement_grace_hours")
                or settlement_spec.get("settlement_grace_hours")
                or settlement_grace_hours_for_source(settlement_source),
                "settlement_rule_hash": row.get("settlement_rule_hash") or settlement_spec.get("rule_hash"),
                "settlement_station_code": row.get("settlement_station_code") or settlement_spec.get("station_code"),
                "settlement_source": settlement_source,
                "settlement_timezone": _timezone_name(row),
                "archive_count": 0,
                "ready_orderbook_count": 0,
                "first_archive_at": None,
                "last_archive_at": None,
            },
        )
        existing["archive_count"] += 1
        if _orderbook_ready(row):
            existing["ready_orderbook_count"] += 1
        if existing.get("end_time") is None and market_close_time is not None:
            existing["end_time"] = _iso(market_close_time)
        if existing.get("market_close_time") is None and market_close_time is not None:
            existing["market_close_time"] = _iso(market_close_time)
        if existing.get("observation_window_end_time") is None and observation_end_time is not None:
            existing["observation_window_end_time"] = _iso(observation_end_time)
        if existing.get("settlement_due_time") is None and settlement_due_time is not None:
            existing["settlement_due_time"] = _iso(settlement_due_time)
        for source_field, target_field in (
            ("bucket_type", "bucket_type"),
            ("target_date", "target_date"),
            ("settlement_rule_hash", "settlement_rule_hash"),
            ("settlement_station_code", "settlement_station_code"),
            ("settlement_source", "settlement_source"),
            ("settlement_timezone", "settlement_timezone"),
        ):
            if existing.get(target_field) is None and row.get(source_field) is not None:
                existing[target_field] = row.get(source_field)
        if recorded_at is not None:
            recorded_iso = _iso(recorded_at)
            if existing["first_archive_at"] is None or recorded_iso < existing["first_archive_at"]:
                existing["first_archive_at"] = recorded_iso
            if existing["last_archive_at"] is None or recorded_iso > existing["last_archive_at"]:
                existing["last_archive_at"] = recorded_iso
    return tokens


def _pending_closed_backfill_status(record: Dict[str, Any], *, now: datetime) -> str:
    settlement_due_time = _parse_utc(record.get("settlement_due_time"))
    if settlement_due_time is None:
        return "missing_settlement_due_time_metadata"
    market_close_time = _parse_utc(record.get("market_close_time") or record.get("end_time"))
    if market_close_time is not None and now < market_close_time:
        return "awaiting_market_close"
    observation_end_time = _parse_utc(record.get("observation_window_end_time"))
    if observation_end_time is not None and now < observation_end_time:
        return "awaiting_observation_window_end"
    if now < settlement_due_time:
        return "awaiting_settlement_due_time"
    return "closed_backfill_due"


def _pending_gap_reason(status: str) -> str:
    return {
        "missing_settlement_due_time_metadata": "archived_orderbook_missing_settlement_due_time_metadata",
        "closed_backfill_due": "archived_orderbook_pending_closed_backfill_due",
        "awaiting_market_close": "archived_orderbook_waiting_for_market_close",
        "awaiting_observation_window_end": "archived_orderbook_waiting_for_observation_window_end",
        "awaiting_settlement_due_time": "archived_orderbook_waiting_for_settlement_due_time",
    }.get(status, "archived_orderbook_pending_closed_backfill")


def _pending_records_by_status(
    archived_by_token: Dict[str, Dict[str, Any]],
    token_ids: Iterable[str],
    *,
    now: datetime,
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {
        "awaiting_market_close": [],
        "awaiting_observation_window_end": [],
        "awaiting_settlement_due_time": [],
        "closed_backfill_due": [],
        "closed_backfill_attempted_but_market_open": [],
        "missing_settlement_due_time_metadata": [],
    }
    for token_id in sorted(token_ids):
        record = dict(archived_by_token[token_id])
        status = _pending_closed_backfill_status(record, now=now)
        record["pending_closed_backfill_status"] = status
        record["gap_reason"] = _pending_gap_reason(status)
        grouped.setdefault(status, []).append(record)
    return grouped


def _market_queries(records: Iterable[Dict[str, Any]]) -> List[str]:
    queries: List[str] = []
    seen = set()
    for record in records:
        query = _text(record.get("market_slug") or record.get("market_id"))
        if not query or query in seen:
            continue
        queries.append(query)
        seen.add(query)
    return queries


def _closed_backfill_command(
    market_queries: Iterable[str],
    *,
    backfill_dir: str = str(DEFAULT_BACKFILL_DIR),
) -> List[str]:
    queries = list(market_queries)
    if not queries:
        return []
    command = [
        "PYTHONPATH=src",
        ".venv/bin/python",
        "scripts/weather_market_closed_backfill.py",
        "--backfill-dir",
        str(backfill_dir),
        "--no-city-temperature-queries",
        "--polymarket-search-limit",
        "5",
    ]
    for query in queries:
        command.extend(["--market-slug", query])
    return command


def _earliest_time(records: Iterable[Dict[str, Any]], field: str) -> Optional[datetime]:
    values = [
        parsed
        for record in records
        for parsed in [_parse_utc(record.get(field))]
        if parsed is not None
    ]
    if not values:
        return None
    return min(values)


def _records_at_time(records: Iterable[Dict[str, Any]], field: str, timestamp: Optional[datetime]) -> List[Dict[str, Any]]:
    if timestamp is None:
        return []
    return [
        row
        for row in records
        if _parse_utc(row.get(field)) == timestamp
    ]


def _wrong_due_prevented(records: Iterable[Dict[str, Any]], *, now: datetime) -> List[Dict[str, Any]]:
    prevented = []
    for record in records:
        status = _text(record.get("pending_closed_backfill_status"))
        if status not in {"awaiting_observation_window_end", "awaiting_settlement_due_time"}:
            continue
        market_close_time = _parse_utc(record.get("market_close_time") or record.get("end_time"))
        if market_close_time is not None and market_close_time <= now:
            prevented.append(record)
    return prevented


def _earliest_future_settlement_due(records: Iterable[Dict[str, Any]], *, now: datetime) -> Optional[datetime]:
    values = [
        parsed
        for record in records
        for parsed in [_parse_utc(record.get("settlement_due_time"))]
        if parsed is not None and parsed > now
    ]
    if not values:
        return None
    return min(values)


def _due_schedule_by_station_date_source(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for record in records:
        station_code = _text(record.get("settlement_station_code")).upper() or "unknown"
        target_date = _text(record.get("target_date")) or "unknown"
        settlement_source = _text(record.get("settlement_source")).lower() or "unknown"
        key = (station_code, target_date, settlement_source)
        row = grouped.setdefault(
            key,
            {
                "station_code": station_code,
                "target_date": target_date,
                "settlement_source": settlement_source,
                "settlement_timezone": record.get("settlement_timezone"),
                "observation_window_end_time": record.get("observation_window_end_time"),
                "settlement_due_time": record.get("settlement_due_time"),
                "market_count": 0,
                "token_count": 0,
                "pending_status_counts": {},
                "cities": [],
                "market_slug_samples": [],
            },
        )
        row["market_count"] += 1
        row["token_count"] += 1
        status = _text(record.get("pending_closed_backfill_status")) or "unknown"
        row["pending_status_counts"][status] = int(row["pending_status_counts"].get(status) or 0) + 1
        city = _text(record.get("city")).lower()
        if city and city not in row["cities"]:
            row["cities"].append(city)
        slug = _text(record.get("market_slug"))
        if slug and len(row["market_slug_samples"]) < 5:
            row["market_slug_samples"].append(slug)
        for field in ("settlement_timezone", "observation_window_end_time", "settlement_due_time"):
            if row.get(field) is None and record.get(field) is not None:
                row[field] = record.get(field)

    rows: List[Dict[str, Any]] = []
    for row in grouped.values():
        row["cities"] = sorted(row["cities"])
        row["pending_status_counts"] = [
            {"status": status, "count": count}
            for status, count in sorted(
                row["pending_status_counts"].items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ]
        rows.append(row)
    rows.sort(
        key=lambda row: (
            str(row.get("settlement_due_time") or ""),
            str(row.get("settlement_source") or ""),
            str(row.get("station_code") or ""),
            str(row.get("target_date") or ""),
        )
    )
    return rows


def build_orderbook_closed_token_coverage_report(
    *,
    closed_records: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
    max_samples: int = 20,
) -> Dict[str, Any]:
    """Compare archived Yes token ids with resolved closed-market Yes token ids.

    This is diagnostic-only. It separates true missing archive coverage for old
    closed markets from newly archived active markets that are simply not in the
    closed/resolved corpus yet.
    """

    now = _parse_utc(generated_at) or _parse_utc(utc_now_iso()) or datetime.now(timezone.utc)
    closed_by_token, closed_gaps = _closed_token_records(closed_records)
    archived_by_token = _archived_token_records(orderbook_snapshots)
    closed_tokens = set(closed_by_token)
    archived_tokens = set(archived_by_token)
    matched_tokens = sorted(closed_tokens & archived_tokens)
    missing_archive_tokens = sorted(closed_tokens - archived_tokens)
    pending_closed_tokens = sorted(archived_tokens - closed_tokens)
    pending_by_status = _pending_records_by_status(
        archived_by_token,
        pending_closed_tokens,
        now=now,
    )
    pending_records = [
        row
        for status in (
            "closed_backfill_due",
            "awaiting_market_close",
            "awaiting_observation_window_end",
            "awaiting_settlement_due_time",
            "closed_backfill_attempted_but_market_open",
            "missing_settlement_due_time_metadata",
        )
        for row in pending_by_status.get(status, [])
    ]

    gaps: List[Dict[str, Any]] = list(closed_gaps)
    gaps.extend(
        {
            **closed_by_token[token_id],
            "gap_reason": "closed_market_missing_archived_orderbook",
        }
        for token_id in missing_archive_tokens
    )
    gaps.extend(pending_records)

    if not archived_by_token and not closed_by_token:
        hard_conclusion = "orderbook_closed_token_coverage_no_inputs"
    elif not archived_by_token:
        hard_conclusion = "orderbook_closed_token_coverage_no_archived_tokens"
    elif not closed_by_token:
        hard_conclusion = "orderbook_closed_token_coverage_no_closed_yes_tokens"
    elif not matched_tokens:
        hard_conclusion = "orderbook_closed_token_coverage_no_overlap"
    elif missing_archive_tokens or pending_closed_tokens:
        hard_conclusion = "orderbook_closed_token_coverage_partial_overlap"
    else:
        hard_conclusion = "orderbook_closed_token_coverage_ready"

    sample_count = max(0, int(max_samples))
    refresh_due = pending_by_status.get("closed_backfill_due", [])
    awaiting_market_close = pending_by_status.get("awaiting_market_close", [])
    awaiting_observation_end = pending_by_status.get("awaiting_observation_window_end", [])
    awaiting_settlement_due = pending_by_status.get("awaiting_settlement_due_time", [])
    attempted_open = pending_by_status.get("closed_backfill_attempted_but_market_open", [])
    missing_settlement_due_metadata = pending_by_status.get("missing_settlement_due_time_metadata", [])
    wrong_due_prevented = _wrong_due_prevented(pending_records, now=now)
    due_queries = _market_queries(refresh_due)
    next_market_close = _earliest_time(awaiting_market_close, "market_close_time")
    next_observation_end = _earliest_time(awaiting_observation_end, "observation_window_end_time")
    next_settlement_due = _earliest_time(awaiting_settlement_due, "settlement_due_time")
    earliest_future_due = _earliest_future_settlement_due(
        [
            *awaiting_market_close,
            *awaiting_observation_end,
            *awaiting_settlement_due,
        ],
        now=now,
    )
    next_market_close_records = _records_at_time(awaiting_market_close, "market_close_time", next_market_close)
    next_observation_end_records = _records_at_time(
        awaiting_observation_end,
        "observation_window_end_time",
        next_observation_end,
    )
    next_settlement_due_records = _records_at_time(awaiting_settlement_due, "settlement_due_time", next_settlement_due)
    next_market_close_queries = _market_queries(next_market_close_records)
    next_observation_end_queries = _market_queries(next_observation_end_records)
    next_settlement_due_queries = _market_queries(next_settlement_due_records)
    next_wait_candidates = [
        value
        for value in (next_market_close, next_observation_end, next_settlement_due)
        if value is not None
    ]
    next_refresh_check_after = _iso(now) if refresh_due else (_iso(min(next_wait_candidates)) if next_wait_candidates else None)
    due_schedule_records = [
        *refresh_due,
        *awaiting_observation_end,
        *awaiting_settlement_due,
    ]
    due_schedule = _due_schedule_by_station_date_source(due_schedule_records)
    return {
        "schema_version": ORDERBOOK_CLOSED_TOKEN_COVERAGE_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "generated_at": _iso(now),
        "closed_yes_token_count": len(closed_tokens),
        "archived_unique_token_count": len(archived_tokens),
        "matched_closed_archived_token_count": len(matched_tokens),
        "unmatched_closed_token_count": len(missing_archive_tokens),
        "unmatched_archived_token_count": len(pending_closed_tokens),
        "pending_closed_backfill_due_token_count": len(refresh_due),
        "pending_closed_backfill_await_market_end_token_count": len(awaiting_market_close),
        "pending_closed_backfill_missing_end_time_token_count": len(missing_settlement_due_metadata),
        "pending_awaiting_market_close_token_count": len(awaiting_market_close),
        "pending_awaiting_observation_window_end_token_count": len(awaiting_observation_end),
        "pending_awaiting_settlement_due_time_token_count": len(awaiting_settlement_due),
        "closed_backfill_due_token_count": len(refresh_due),
        "closed_backfill_attempted_but_market_open_token_count": len(attempted_open),
        "missing_settlement_due_time_metadata_token_count": len(missing_settlement_due_metadata),
        "wrong_due_prevented_count": len(wrong_due_prevented),
        "closed_missing_yes_token_count": len(closed_gaps),
        "gap_count": len(gaps),
        "gaps_by_reason": _count_by(gaps, "gap_reason", key_name="reason"),
        "closed_backfill_followup_plan": {
            "schema_version": ORDERBOOK_CLOSED_BACKFILL_FOLLOWUP_SCHEMA_VERSION,
            "paper_only": True,
            "diagnostic_only": True,
            "counts_for_live_gate": False,
            "generated_at": _iso(now),
            "refresh_due_count": len(refresh_due),
            "closed_backfill_due_count": len(refresh_due),
            "await_market_end_count": len(awaiting_market_close),
            "awaiting_market_close_count": len(awaiting_market_close),
            "awaiting_observation_window_end_count": len(awaiting_observation_end),
            "awaiting_settlement_due_time_count": len(awaiting_settlement_due),
            "closed_backfill_attempted_but_market_open_count": len(attempted_open),
            "missing_end_time_count": len(missing_settlement_due_metadata),
            "missing_settlement_due_time_metadata_count": len(missing_settlement_due_metadata),
            "wrong_due_prevented_count": len(wrong_due_prevented),
            "request_count": len(refresh_due),
            "market_query_count": len(due_queries),
            "next_await_market_end": _iso(next_market_close) if next_market_close else None,
            "next_market_close_check_after": _iso(next_market_close) if next_market_close else None,
            "next_observation_window_end_after": _iso(next_observation_end) if next_observation_end else None,
            "next_settlement_due_check_after": (
                _iso(next_settlement_due or earliest_future_due)
                if (next_settlement_due or earliest_future_due)
                else None
            ),
            "earliest_settlement_due_time": _iso(earliest_future_due) if earliest_future_due else None,
            "next_refresh_check_after": next_refresh_check_after,
            "next_await_market_end_token_count": len(next_market_close_records),
            "next_await_market_query_count": len(next_market_close_queries),
            "next_action": (
                "refresh_closed_weather_backfill_for_due_archived_markets"
                if refresh_due
                else (
                    "wait_for_archived_markets_to_reach_market_close"
                    if awaiting_market_close
                    else (
                        "wait_for_observation_window_end"
                        if awaiting_observation_end
                        else (
                            "wait_for_settlement_due_time"
                            if awaiting_settlement_due
                            else "preserve_settlement_due_time_metadata_in_future_orderbook_archives"
                        )
                    )
                )
            ),
            "market_queries": due_queries[:sample_count],
            "next_due_market_queries": due_queries[:sample_count],
            "next_await_market_queries": next_market_close_queries[:sample_count],
            "next_market_close_queries": next_market_close_queries[:sample_count],
            "next_observation_window_end_queries": next_observation_end_queries[:sample_count],
            "next_settlement_due_queries": next_settlement_due_queries[:sample_count],
            "recommended_command": _closed_backfill_command(due_queries),
            "due_schedule_by_station_date_source": due_schedule,
            "requests": refresh_due[:sample_count],
            "await_market_end_samples": awaiting_market_close[:sample_count],
            "awaiting_market_close_samples": awaiting_market_close[:sample_count],
            "awaiting_observation_window_end_samples": awaiting_observation_end[:sample_count],
            "awaiting_settlement_due_time_samples": awaiting_settlement_due[:sample_count],
            "wrong_due_prevented_samples": wrong_due_prevented[:sample_count],
            "closed_backfill_attempted_but_market_open_samples": attempted_open[:sample_count],
            "missing_end_time_samples": missing_settlement_due_metadata[:sample_count],
            "missing_settlement_due_time_metadata_samples": missing_settlement_due_metadata[:sample_count],
        },
        "matched_samples": [
            {
                "token_id": token_id,
                "closed_market_slug": closed_by_token[token_id].get("market_slug"),
                "archived_market_slug": archived_by_token[token_id].get("market_slug"),
                "last_archive_at": archived_by_token[token_id].get("last_archive_at"),
                "archive_count": archived_by_token[token_id].get("archive_count"),
            }
            for token_id in matched_tokens[:sample_count]
        ],
        "closed_markets_missing_archive_samples": [
            closed_by_token[token_id]
            for token_id in missing_archive_tokens[:sample_count]
        ],
        "archived_markets_pending_closed_backfill_samples": [
            row
            for row in pending_records[:sample_count]
        ],
        "closed_missing_token_samples": closed_gaps[:sample_count],
        "hard_conclusion": hard_conclusion,
    }


def build_orderbook_archive_coverage_report(
    payload: Dict[str, Any],
    *,
    archive_rows: Optional[Iterable[Dict[str, Any]]] = None,
    generated_at: Optional[str] = None,
    max_gap_samples: int = 20,
) -> Dict[str, Any]:
    now = _parse_utc(generated_at or payload.get("generated_at")) or _parse_utc(utc_now_iso())
    if now is None:
        now = datetime.now(timezone.utc)
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    archives = [row for row in (archive_rows or []) if isinstance(row, dict)]
    archive_by_token = _archived_tokens(archives, now=now)
    eligible: List[Dict[str, Any]] = []
    covered: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []

    for row in rows:
        side = _side(row)
        if side not in {"yes", "y"}:
            continue
        token_id = _text(row.get("token_id"))
        market_close_time = _market_close_time(row)
        active = row.get("active") is not False
        closed = row.get("closed") is True
        accepting = row.get("accepting_orders") is not False
        tradable = row.get("tradable") is not False
        base = {
            "market_id": row.get("market_id"),
            "market_slug": row.get("market_slug"),
            "token_id": token_id or None,
            "city": row.get("city"),
            "bucket_label": row.get("bucket_label"),
            "end_time": _iso(market_close_time) if market_close_time else None,
            "market_close_time": _iso(market_close_time) if market_close_time else None,
            "observation_window_end_time": (
                _iso(_observation_window_end_time(row)) if _observation_window_end_time(row) else None
            ),
            "settlement_due_time": _iso(_settlement_due_time(row)) if _settlement_due_time(row) else None,
        }
        if not token_id:
            gaps.append({**base, "gap_reason": "missing_token_id"})
            continue
        if market_close_time is None:
            gaps.append({**base, "gap_reason": "missing_market_close_time"})
            continue
        if market_close_time <= now:
            gaps.append({**base, "gap_reason": "market_already_ended"})
            continue
        if closed or not active or not accepting or not tradable:
            gaps.append({**base, "gap_reason": "not_active_tradable_accepting"})
            continue
        if not _orderbook_ready(row):
            gaps.append({**base, "gap_reason": "missing_current_orderbook_depth"})
            continue

        record = {
            **base,
            "minutes_to_end": round((market_close_time - now).total_seconds() / 60.0, 2),
            "best_ask": _safe_float((row.get("order_book") or {}).get("best_ask") if isinstance(row.get("order_book"), dict) else row.get("best_ask")),
            "best_bid": _safe_float((row.get("order_book") or {}).get("best_bid") if isinstance(row.get("order_book"), dict) else row.get("best_bid")),
            "spread": _safe_float((row.get("order_book") or {}).get("spread") if isinstance(row.get("order_book"), dict) else row.get("spread")),
        }
        eligible.append(record)
        archive_times = [
            time
            for time in archive_by_token.get(token_id, [])
            if time <= market_close_time
        ]
        if archive_times:
            covered.append(
                {
                    **record,
                    "latest_archive_at": _iso(max(archive_times)),
                    "archive_count": len(archive_times),
                }
            )
        else:
            gaps.append({**record, "gap_reason": "missing_preresolution_archive"})

    eligible_count = len(eligible)
    covered_count = len(covered)
    if eligible_count <= 0:
        hard_conclusion = "orderbook_archive_coverage_no_active_eligible_markets"
    elif covered_count < eligible_count:
        hard_conclusion = "orderbook_archive_coverage_incomplete"
    else:
        hard_conclusion = "orderbook_archive_coverage_ready"
    return {
        "schema_version": ORDERBOOK_ARCHIVE_COVERAGE_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "generated_at": _iso(now),
        "source_snapshot_id": payload.get("snapshot_id"),
        "row_count": len(rows),
        "archive_row_count": len(archives),
        "active_yes_token_count": len([row for row in rows if _side(row) in {"yes", "y"}]),
        "eligible_preresolution_count": eligible_count,
        "covered_preresolution_count": covered_count,
        "coverage_rate": round(covered_count / eligible_count, 6) if eligible_count else None,
        "gap_count": len(gaps),
        "gaps_by_reason": _count_by(gaps, "gap_reason", key_name="reason"),
        "covered_samples": covered[: max(0, int(max_gap_samples))],
        "gap_samples": gaps[: max(0, int(max_gap_samples))],
        "hard_conclusion": hard_conclusion,
    }


def build_orderbook_archive_coverage_report_from_dir(
    payload: Dict[str, Any],
    *,
    archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    generated_at: Optional[str] = None,
    max_gap_samples: int = 20,
) -> Dict[str, Any]:
    return build_orderbook_archive_coverage_report(
        payload,
        archive_rows=load_jsonl(Path(archive_dir) / "orderbook_snapshots.jsonl"),
        generated_at=generated_at,
        max_gap_samples=max_gap_samples,
    )
