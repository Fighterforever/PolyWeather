from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_readonly import (
    DEFAULT_WEATHER_QUERIES,
    PolymarketReadonlyClient,
    _parse_json_list,
    build_polymarket_closed_weather_payload,
)
from src.trading.weather_market_enrichment import parse_temperature_outcome_spec
from src.trading.weather_paper_journal import (
    _append_jsonl,
    _safe_float,
    _write_json_atomic,
    load_jsonl,
    stable_json_hash,
    utc_now_iso,
)


BACKFILL_SCHEMA_VERSION = "polyweather_weather_closed_backfill.v1"
DEFAULT_BACKFILL_DIR = Path("data/trading/weather_backfill")


def _group_rows_by_market(rows: Iterable[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = str(row.get("market_id") or row.get("market_slug") or f"row:{index}")
        grouped.setdefault(key, []).append(row)
    return list(grouped.values())


def _row_payout(row: Dict[str, Any]) -> Optional[float]:
    return _safe_float(row.get("market_probability") if row.get("market_probability") is not None else row.get("price"))


def _resolution_status(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "missing_rows"
    if not any(row.get("closed") is True for row in rows):
        return "not_closed"
    winners = [row for row in rows if (_row_payout(row) or 0.0) >= 0.999]
    if len(winners) == 1:
        return "resolved"
    if len(winners) > 1:
        return "ambiguous_resolution"
    return "missing_winning_outcome"


def _settled_probability_by_outcome(rows: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    values: Dict[str, Optional[float]] = {}
    for row in rows:
        outcome = str(row.get("outcome") or row.get("side") or "").strip() or "unknown"
        values[outcome] = _row_payout(row)
    return values


def _closed_backfill_dedupe_key(record: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(record.get("market_id") or "").strip(),
        str(record.get("market_slug") or "").strip(),
        str(record.get("winning_outcome") or "").strip().lower(),
    )


def _filter_duplicate_closed_records(
    records: Iterable[Dict[str, Any]],
    *,
    existing_records: Iterable[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    existing_keys = {
        _closed_backfill_dedupe_key(record)
        for record in existing_records
        if isinstance(record, dict)
    }
    filtered: List[Dict[str, Any]] = []
    duplicate_count = 0
    seen_new = set()
    for record in records:
        key = _closed_backfill_dedupe_key(record)
        if key in existing_keys or key in seen_new:
            duplicate_count += 1
            continue
        filtered.append(record)
        seen_new.add(key)
    return filtered, duplicate_count


def build_closed_backfill_records(
    payload: Dict[str, Any],
    *,
    recorded_at: Optional[str] = None,
    max_records: Optional[int] = None,
) -> List[Dict[str, Any]]:
    recorded_at = recorded_at or utc_now_iso()
    rows = payload.get("rows") if isinstance(payload, dict) else []
    market_groups = _group_rows_by_market(rows or [])
    if max_records is not None:
        market_groups = market_groups[: max(0, int(max_records))]

    records: List[Dict[str, Any]] = []
    for group in market_groups:
        first = group[0]
        spec = parse_temperature_outcome_spec(first)
        parsed_spec = None
        if spec is not None:
            parsed_spec = {
                "city": spec.city,
                "target_date": spec.target_date,
                "threshold": spec.threshold,
                "upper_threshold": spec.upper_threshold,
                "comparator": spec.comparator,
                "unit": spec.unit,
                "market_type": spec.market_type,
            }
        status = _resolution_status(group)
        winning_rows = [row for row in group if (_row_payout(row) or 0.0) >= 0.999]
        winning_row = winning_rows[0] if len(winning_rows) == 1 else None
        record_id = stable_json_hash(
            {
                "source_snapshot_id": payload.get("snapshot_id"),
                "market_id": first.get("market_id"),
                "market_slug": first.get("market_slug"),
                "winning_token_id": (winning_row or {}).get("token_id"),
                "status": status,
            },
            length=24,
        )
        records.append(
            {
                "schema_version": BACKFILL_SCHEMA_VERSION,
                "record_id": record_id,
                "recorded_at": recorded_at,
                "backfill_only": True,
                "paper_only": True,
                "counts_for_live_gate": False,
                "evidence_class": "closed_market_backfill",
                "status": status,
                "source_snapshot_id": payload.get("snapshot_id"),
                "source": payload.get("source"),
                "event_id": first.get("event_id"),
                "event_slug": first.get("event_slug"),
                "event_title": first.get("event_title"),
                "market_id": first.get("market_id"),
                "market_slug": first.get("market_slug"),
                "question": first.get("question"),
                "city": parsed_spec.get("city") if parsed_spec else None,
                "target_date": parsed_spec.get("target_date") if parsed_spec else None,
                "bucket_label": (
                    f"{parsed_spec['threshold']:g}-{parsed_spec['upper_threshold']:g}°{parsed_spec['unit']}"
                    if parsed_spec and parsed_spec.get("comparator") == "range" and parsed_spec.get("upper_threshold") is not None
                    else (
                        f"{ {'ge': '>=', 'le': '<=', 'eq': '='}.get(parsed_spec['comparator'], '?')} {parsed_spec['threshold']:g}°{parsed_spec['unit']}"
                        if parsed_spec
                        else None
                    )
                ),
                "parsed_temperature_spec": parsed_spec,
                "outcomes": [row.get("outcome") for row in group],
                "settled_probability_by_outcome": _settled_probability_by_outcome(group),
                "winning_outcome": (winning_row or {}).get("outcome"),
                "winning_side": (winning_row or {}).get("side"),
                "winning_token_id": (winning_row or {}).get("token_id"),
                "winning_payout": _row_payout(winning_row) if winning_row else None,
                "market_closed": any(row.get("closed") is True for row in group),
                "row_count": len(group),
            }
        )
    return records


def write_closed_backfill_journal(
    payload: Dict[str, Any],
    *,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    max_records: Optional[int] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    backfill_root = Path(backfill_dir)
    recorded_at = recorded_at or utc_now_iso()
    run_id = stable_json_hash(
        {
            "schema_version": payload.get("schema_version"),
            "snapshot_id": payload.get("snapshot_id"),
            "diagnostics": payload.get("diagnostics"),
            "recorded_at": recorded_at,
        },
        length=20,
    )
    snapshot_path = backfill_root / "snapshots" / f"{run_id}.json"
    records_path = backfill_root / "closed_markets.jsonl"
    manifest_path = backfill_root / "manifest.jsonl"
    records = build_closed_backfill_records(
        payload,
        recorded_at=recorded_at,
        max_records=max_records,
    )
    records, duplicate_skipped_count = _filter_duplicate_closed_records(
        records,
        existing_records=load_jsonl(records_path),
    )
    snapshot_payload = {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_at": recorded_at,
        "payload": payload,
    }
    _write_json_atomic(snapshot_path, snapshot_payload)
    written = _append_jsonl(records_path, records)
    summary = summarize_closed_backfill_records(records)
    manifest_record = {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_at": recorded_at,
        "snapshot_path": str(snapshot_path),
        "records_path": str(records_path),
        "record_count": written,
        "duplicate_skipped_count": duplicate_skipped_count,
        **summary,
    }
    _append_jsonl(manifest_path, [manifest_record])
    return {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_at": recorded_at,
        "backfill_dir": str(backfill_root),
        "snapshot_path": str(snapshot_path),
        "records_path": str(records_path),
        "manifest_path": str(manifest_path),
        "record_count": written,
        "duplicate_skipped_count": duplicate_skipped_count,
        "backfill_only": True,
        "counts_for_live_gate": False,
        **summary,
    }


def summarize_closed_backfill_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [record for record in records if isinstance(record, dict)]
    resolved = [record for record in rows if record.get("status") == "resolved"]
    parsed = [record for record in rows if isinstance(record.get("parsed_temperature_spec"), dict)]
    return {
        "backfill_record_count": len(rows),
        "backfill_resolved_count": len(resolved),
        "backfill_parsed_temperature_count": len(parsed),
        "backfill_unsupported_count": len(rows) - len(parsed),
        "backfill_unique_city_count": len(
            {record.get("city") for record in parsed if record.get("city")}
        ),
        "backfill_counts_for_live_gate": False,
    }


def summarize_closed_backfill_journal(
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
) -> Dict[str, Any]:
    backfill_root = Path(backfill_dir)
    records = load_jsonl(backfill_root / "closed_markets.jsonl")
    manifest = load_jsonl(backfill_root / "manifest.jsonl")
    summary = summarize_closed_backfill_records(records)
    return {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "backfill_dir": str(backfill_root),
        "manifest_count": len(manifest),
        **summary,
    }


def run_closed_weather_backfill(
    *,
    queries: Iterable[str] = DEFAULT_WEATHER_QUERIES,
    row_limit: int = 100,
    search_limit_per_query: int = 25,
    include_city_temperature_queries: bool = True,
    city_search_limit_per_query: int = 5,
    max_city_temperature_queries: Optional[int] = None,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    max_records: Optional[int] = None,
    client: Optional[PolymarketReadonlyClient] = None,
) -> Dict[str, Any]:
    payload = build_polymarket_closed_weather_payload(
        queries=queries,
        row_limit=row_limit,
        search_limit_per_query=search_limit_per_query,
        include_city_temperature_queries=include_city_temperature_queries,
        city_search_limit_per_query=city_search_limit_per_query,
        max_city_temperature_queries=max_city_temperature_queries,
        client=client,
    )
    journal = write_closed_backfill_journal(
        payload,
        backfill_dir=backfill_dir,
        max_records=max_records,
    )
    return {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "payload_status": payload.get("status"),
        "payload_snapshot_id": payload.get("snapshot_id"),
        "payload_diagnostics": payload.get("diagnostics"),
        "backfill_journal": journal,
        "backfill_summary": summarize_closed_backfill_journal(backfill_dir),
    }


def dump_backfill_result(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
