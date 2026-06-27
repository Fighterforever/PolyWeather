from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_readonly import (
    DEFAULT_WEATHER_QUERIES,
    PolymarketReadonlyClient,
    _parse_json_list,
    build_polymarket_closed_weather_payload,
    is_weather_like_market,
)
from src.trading.weather_market_enrichment import parse_temperature_outcome_spec
from src.trading.weather_market_implied import enrich_payload_with_market_implied
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


def _dedupe_text(values: Iterable[Any]) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        rows.append(text)
        seen.add(text)
    return rows


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


def _token_id_by_outcome(rows: Iterable[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    values: Dict[str, Optional[str]] = {}
    for row in rows:
        outcome = str(row.get("outcome") or row.get("side") or "").strip() or "unknown"
        token_id = str(row.get("token_id") or "").strip()
        values[outcome] = token_id or None
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
        settlement_spec = first.get("settlement_spec") if isinstance(first.get("settlement_spec"), dict) else {}
        status = _resolution_status(group)
        winning_rows = [row for row in group if (_row_payout(row) or 0.0) >= 0.999]
        winning_row = winning_rows[0] if len(winning_rows) == 1 else None
        token_id_by_outcome = _token_id_by_outcome(group)
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
                "token_id_by_outcome": token_id_by_outcome,
                "resolution_source": (
                    first.get("resolution_source")
                    or settlement_spec.get("settlement_source")
                    or payload.get("source")
                ),
                "rule_text": settlement_spec.get("rule_text"),
                "rule_hash": settlement_spec.get("rule_hash"),
                "settlement_spec": settlement_spec or None,
                "official_final_value": first.get("official_final_value"),
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


def build_targeted_closed_weather_payload_from_market_slugs(
    *,
    market_slugs: Iterable[str],
    client: Optional[PolymarketReadonlyClient] = None,
    search_limit_per_slug: int = 5,
) -> Dict[str, Any]:
    client = client or PolymarketReadonlyClient()
    slugs = _dedupe_text(market_slugs)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {
        "requested_market_slug_count": len(slugs),
        "matched_market_slug_count": 0,
        "closed_market_slug_count": 0,
        "open_market_slug_count": 0,
        "missing_market_slug_count": 0,
        "non_weather_market_slug_count": 0,
        "errors": [],
        "matched_market_slugs": [],
        "open_market_slugs": [],
        "missing_market_slugs": [],
        "non_weather_market_slugs": [],
    }

    for slug in slugs:
        try:
            events = client.public_search_events(slug, limit=max(1, int(search_limit_per_slug)))
        except Exception as exc:  # pragma: no cover - exact client exception type is not required for fake clients.
            diagnostics["errors"].append(f"{slug}: {exc}")
            continue
        exact_market: Optional[Dict[str, Any]] = None
        exact_event: Optional[Dict[str, Any]] = None
        for event in events:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                if str(market.get("slug") or "").strip() == slug:
                    exact_event = event
                    exact_market = market
                    break
            if exact_market is not None:
                break
        if exact_market is None or exact_event is None:
            diagnostics["missing_market_slug_count"] += 1
            diagnostics["missing_market_slugs"].append(slug)
            continue
        diagnostics["matched_market_slug_count"] += 1
        diagnostics["matched_market_slugs"].append(slug)
        if not is_weather_like_market(exact_event, exact_market):
            diagnostics["non_weather_market_slug_count"] += 1
            diagnostics["non_weather_market_slugs"].append(slug)
            continue
        if exact_market.get("closed") is not True and exact_event.get("closed") is not True:
            diagnostics["open_market_slug_count"] += 1
            diagnostics["open_market_slugs"].append(slug)
            continue
        diagnostics["closed_market_slug_count"] += 1
        rows.extend(
            client.market_to_signal_rows(
                exact_event,
                exact_market,
                include_order_books=False,
            )
        )

    diagnostics["rows"] = len(rows)
    status = "ready" if rows else "no_targeted_closed_weather_markets"
    if diagnostics.get("errors") and not rows:
        status = "partial_error"
    return enrich_payload_with_market_implied(
        {
            "schema_version": "polyweather_polymarket_readonly_payload.v1",
            "snapshot_id": f"polymarket-closed-targeted-readonly-{generated_at}",
            "generated_at": generated_at,
            "status": status,
            "source": "polymarket_closed_targeted_readonly",
            "rows": rows,
            "diagnostics": diagnostics,
        }
    )


def run_targeted_closed_weather_backfill_from_market_slugs(
    *,
    market_slugs: Iterable[str],
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    max_records: Optional[int] = None,
    search_limit_per_slug: int = 5,
    client: Optional[PolymarketReadonlyClient] = None,
) -> Dict[str, Any]:
    payload = build_targeted_closed_weather_payload_from_market_slugs(
        market_slugs=market_slugs,
        client=client,
        search_limit_per_slug=search_limit_per_slug,
    )
    journal = write_closed_backfill_journal(
        payload,
        backfill_dir=backfill_dir,
        max_records=max_records,
    )
    return {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "targeted": True,
        "payload_status": payload.get("status"),
        "payload_snapshot_id": payload.get("snapshot_id"),
        "payload_diagnostics": payload.get("diagnostics"),
        "backfill_journal": journal,
        "backfill_summary": summarize_closed_backfill_journal(backfill_dir),
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
