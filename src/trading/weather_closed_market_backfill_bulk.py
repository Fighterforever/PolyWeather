from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_readonly import (
    DEFAULT_WEATHER_QUERIES,
    PolymarketReadonlyClient,
    PolymarketReadonlyError,
    classify_weather_market_family,
    is_weather_like_event,
    is_weather_like_market,
)


SCHEMA_VERSION = "polyweather_closed_weather_market_bulk.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [dict(row) for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(materialized)


def load_closed_weather_markets(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def collect_closed_weather_events_by_offset(
    *,
    client: PolymarketReadonlyClient,
    limit: int = 500,
    page_size: int = 100,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    events: List[Dict[str, Any]] = []
    errors: List[str] = []
    page_size = max(1, min(100, int(page_size)))
    max_limit = max(0, int(limit))
    for offset in range(0, max_limit, page_size):
        try:
            payload = client._get_json(  # noqa: SLF001 - internal readonly Gamma wrapper.
                client.gamma_base_url,
                "/events",
                {"closed": "true", "limit": page_size, "offset": offset},
            )
        except PolymarketReadonlyError as exc:
            errors.append(str(exc))
            break
        if not isinstance(payload, list):
            errors.append("closed_events_non_list_response")
            break
        if not payload:
            break
        events.extend(event for event in payload if isinstance(event, dict) and is_weather_like_event(event))
        if len(payload) < page_size:
            break
    return events, errors


def _market_key(row: Dict[str, Any]) -> str:
    return _text(row.get("market_id") or row.get("market_slug"))


def _group_rows(rows: Iterable[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _market_key(row)
        if key:
            grouped.setdefault(key, []).append(row)
    return list(grouped.values())


def _winning_outcome(rows: List[Dict[str, Any]]) -> Optional[str]:
    best_outcome = None
    best_prob = -1.0
    for row in rows:
        probability = _safe_float(row.get("market_probability") if row.get("market_probability") is not None else row.get("price"))
        if probability is None:
            continue
        if probability > best_prob:
            best_prob = probability
            best_outcome = _text(row.get("outcome") or row.get("side"))
    if best_prob >= 0.99:
        return best_outcome
    return None


def _record_from_group(rows: List[Dict[str, Any]], *, generated_at: str) -> Dict[str, Any]:
    first = rows[0]
    spec = first.get("settlement_spec") if isinstance(first.get("settlement_spec"), dict) else {}
    token_id_by_outcome = {
        _text(row.get("outcome") or row.get("side")): _text(row.get("token_id")) or None
        for row in rows
        if _text(row.get("outcome") or row.get("side"))
    }
    settled_probability_by_outcome = {
        _text(row.get("outcome") or row.get("side")): _safe_float(
            row.get("market_probability") if row.get("market_probability") is not None else row.get("price")
        )
        for row in rows
        if _text(row.get("outcome") or row.get("side"))
    }
    winning = _winning_outcome(rows)
    winning_token = token_id_by_outcome.get(winning or "") if winning else None
    gaps: List[str] = []
    if not token_id_by_outcome or any(not token for token in token_id_by_outcome.values()):
        gaps.append("incomplete_token_id_by_outcome")
    if not winning:
        gaps.append("missing_winning_outcome")
    if not isinstance(spec, dict) or spec.get("status") != "supported":
        gaps.append("unsupported_settlement_spec")
    if str(first.get("market_family") or "").lower() != "temperature":
        gaps.append("unsupported_weather_family")
    if _text(spec.get("settlement_source")).lower() != "metar":
        gaps.append("non_metar_settlement_source")
    supported_for_replay = not gaps
    yes_token = token_id_by_outcome.get("Yes") or token_id_by_outcome.get("yes")
    settled_yes = settled_probability_by_outcome.get("Yes")
    if settled_yes is None:
        settled_yes = settled_probability_by_outcome.get("yes")
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "market_id": first.get("market_id"),
        "market_slug": first.get("market_slug"),
        "event_slug": first.get("event_slug"),
        "event_title": first.get("event_title"),
        "question": first.get("question"),
        "market_family": first.get("market_family"),
        "outcomes": sorted(token_id_by_outcome),
        "token_id_by_outcome": token_id_by_outcome,
        "winning_outcome": winning,
        "winning_token_id": winning_token,
        "settled_probability_by_outcome": settled_probability_by_outcome,
        "closed_at": first.get("end_date"),
        "end_time": spec.get("market_close_time") or first.get("end_date"),
        "settlement_spec": spec,
        "station_code": spec.get("station_code"),
        "target_date": spec.get("target_date"),
        "bucket_type": spec.get("bucket_type"),
        "threshold": spec.get("threshold"),
        "upper_threshold": spec.get("upper_threshold"),
        "settlement_source": spec.get("settlement_source"),
        "rule_hash": spec.get("rule_hash"),
        "official_final_value": first.get("official_final_value"),
        "token_id": yes_token,
        "settled_yes_payout": settled_yes,
        "status": "resolved" if winning else "closed_unresolved_payload",
        "supported_for_replay": supported_for_replay,
        "gap_reasons": sorted(set(gaps)),
    }


def build_closed_weather_market_bulk_report(
    *,
    client: Optional[PolymarketReadonlyClient] = None,
    event_limit: int = 500,
    page_size: int = 100,
    generated_at: Optional[str] = None,
    include_search: bool = True,
) -> Dict[str, Any]:
    generated_at = generated_at or _now_iso()
    client = client or PolymarketReadonlyClient()
    events, errors = collect_closed_weather_events_by_offset(client=client, limit=event_limit, page_size=page_size)
    if include_search:
        try:
            search_events, search_diagnostics = client.collect_weather_events_from_search(
                queries=DEFAULT_WEATHER_QUERIES,
                search_limit_per_query=100,
                include_city_temperature_queries=True,
                city_search_limit_per_query=20,
                max_city_temperature_queries=None,
            )
            errors.extend(search_diagnostics.get("errors") or [])
            by_key = {_text(event.get("id") or event.get("slug")): event for event in events if _text(event.get("id") or event.get("slug"))}
            for event in search_events:
                key = _text(event.get("id") or event.get("slug"))
                if key and (event.get("closed") is True or any((market or {}).get("closed") is True for market in event.get("markets") or [])):
                    by_key[key] = event
            events = list(by_key.values())
        except Exception as exc:
            errors.append(f"closed_weather_search_merge_failed: {exc}")
    raw_rows: List[Dict[str, Any]] = []
    markets_seen = 0
    weather_markets_seen = 0
    for event in events:
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        for market in markets:
            if not isinstance(market, dict):
                continue
            markets_seen += 1
            if not is_weather_like_market(event, market):
                continue
            weather_markets_seen += 1
            raw_rows.extend(client.market_to_signal_rows(event, market, include_order_books=False))
    records = [_record_from_group(group, generated_at=generated_at) for group in _group_rows(raw_rows)]
    temperature_records = [record for record in records if str(record.get("market_family") or "").lower() == "temperature"]
    gaps = [
        {
            "market_slug": record.get("market_slug"),
            "market_id": record.get("market_id"),
            "station_code": record.get("station_code"),
            "target_date": record.get("target_date"),
            "gap_reason": reason,
        }
        for record in records
        for reason in record.get("gap_reasons") or []
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "event_limit": int(event_limit),
        "page_size": int(page_size),
        "closed_weather_event_count": len(events),
        "markets_seen": markets_seen,
        "weather_markets_seen": weather_markets_seen,
        "closed_weather_market_count": len(records),
        "closed_temperature_market_count": len(temperature_records),
        "replay_supported_market_count": len([record for record in records if record.get("supported_for_replay")]),
        "errors": errors,
        "records": records,
        "gaps": gaps,
    }


def write_closed_weather_market_bulk_artifacts(
    report: Dict[str, Any],
    *,
    output_path: str | Path,
    manifest_path: str | Path,
    gap_report_path: str | Path,
) -> Dict[str, Any]:
    records = [row for row in report.get("records") or [] if isinstance(row, dict)]
    gaps = [row for row in report.get("gaps") or [] if isinstance(row, dict)]
    written = _write_jsonl(output_path, records)
    manifest = {
        "schema_version": "polyweather_closed_weather_market_bulk_manifest.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": report.get("generated_at"),
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
        "gap_report_path": str(gap_report_path),
        "closed_weather_market_count": len(records),
        "closed_temperature_market_count": report.get("closed_temperature_market_count"),
        "replay_supported_market_count": report.get("replay_supported_market_count"),
        "written_count": written,
        "station_date_source_joinable_count": len(
            [
                record
                for record in records
                if record.get("station_code") and record.get("target_date") and record.get("settlement_source")
            ]
        ),
        "error_count": len(report.get("errors") or []),
    }
    gap_report = {
        "schema_version": "polyweather_closed_weather_market_bulk_gap_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": report.get("generated_at"),
        "gap_count": len(gaps),
        "gaps": gaps,
        "errors": report.get("errors") or [],
    }
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    Path(gap_report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(gap_report_path).write_text(json.dumps(gap_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"manifest": manifest, "gap_report": gap_report}
