from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_readonly import (
    PolymarketReadonlyClient,
    PolymarketReadonlyError,
    _parse_json_list,
)
from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _append_jsonl,
    _parse_utc_iso,
    _safe_float,
    load_jsonl,
    stable_json_hash,
    utc_now_iso,
)
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR


RESOLVED_AUDIT_SCHEMA_VERSION = "polyweather_weather_resolved_audit.v1"
RESOLVED_GAP_SCHEMA_VERSION = "polyweather_weather_resolved_gap.v1"


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _market_resolution_status(market: Dict[str, Any]) -> str:
    if market.get("closed") is not True:
        return "unresolved"
    status = _normalized_text(market.get("umaResolutionStatus"))
    if status and status not in {"resolved", "settled"}:
        return status
    return "resolved"


def _outcome_index_for_fill(fill: Dict[str, Any], market: Dict[str, Any]) -> Optional[int]:
    token_id = str(fill.get("token_id") or "").strip()
    token_ids = [str(item) for item in _parse_json_list(market.get("clobTokenIds"))]
    if token_id and token_id in token_ids:
        return token_ids.index(token_id)

    outcome = _normalized_text(fill.get("outcome") or fill.get("side"))
    outcomes = [_normalized_text(item) for item in _parse_json_list(market.get("outcomes"))]
    if outcome and outcome in outcomes:
        return outcomes.index(outcome)
    side = _normalized_text(fill.get("side"))
    if side and side in outcomes:
        return outcomes.index(side)
    return None


def build_resolved_audit_record(
    fill: Dict[str, Any],
    market: Optional[Dict[str, Any]],
    *,
    recorded_at: Optional[str] = None,
    error: Optional[str] = None,
    resolution_source: str = "polymarket_api",
) -> Dict[str, Any]:
    recorded_at = recorded_at or utc_now_iso()
    entry_price = _safe_float(fill.get("entry_price"))
    status = "error" if error else "missing_market"
    payout: Optional[float] = None
    winning = None
    market_closed = None
    market_resolution_status = None
    outcome_index = None
    outcome_prices: List[Any] = []
    market_id = fill.get("market_id")
    market_slug = fill.get("market_slug")

    if isinstance(market, dict):
        market_id = market.get("id") or market_id
        market_slug = market.get("slug") or market_slug
        market_closed = bool(market.get("closed"))
        market_resolution_status = _market_resolution_status(market)
        status = market_resolution_status
        outcome_prices = _parse_json_list(market.get("outcomePrices"))
        outcome_index = _outcome_index_for_fill(fill, market)
        if status == "resolved" and outcome_index is not None and outcome_index < len(outcome_prices):
            payout = _safe_float(outcome_prices[outcome_index])
            if payout is None:
                status = "missing_payout"
            else:
                winning = payout >= 0.999
        elif status == "resolved":
            status = "missing_outcome_match"

    pnl_cents = (
        round((payout - entry_price) * 100.0, 6)
        if payout is not None and entry_price is not None
        else None
    )
    roi_pct = (
        round((payout / entry_price - 1.0) * 100.0, 6)
        if payout is not None and entry_price not in (None, 0)
        else None
    )
    audit_id = stable_json_hash(
        {
            "fill_id": fill.get("fill_id"),
            "recorded_at": recorded_at,
            "market_id": market_id,
            "status": status,
            "payout": payout,
        },
        length=24,
    )
    return {
        "schema_version": RESOLVED_AUDIT_SCHEMA_VERSION,
        "audit_id": audit_id,
        "fill_id": fill.get("fill_id"),
        "run_id": fill.get("run_id"),
        "recorded_at": recorded_at,
        "paper_only": True,
        "resolution_source": resolution_source,
        "status": status,
        "error": error,
        "market_id": market_id,
        "market_slug": market_slug,
        "market_closed": market_closed,
        "market_resolution_status": market_resolution_status,
        "question": (market or {}).get("question") if isinstance(market, dict) else fill.get("question"),
        "token_id": fill.get("token_id"),
        "side": fill.get("side"),
        "outcome": fill.get("outcome"),
        "outcome_index": outcome_index,
        "outcome_prices": outcome_prices,
        "entry_price": entry_price,
        "payout": payout,
        "winning": winning,
        "pnl_cents": pnl_cents,
        "roi_pct": roi_pct,
        "entry_recorded_at": fill.get("recorded_at"),
        "edge_percent_at_entry": _safe_float(fill.get("edge_percent")),
        "model_probability_at_entry": _safe_float(fill.get("model_probability")),
        "end_date": fill.get("end_date"),
    }


def resolve_market_for_fill(
    fill: Dict[str, Any],
    *,
    client: PolymarketReadonlyClient,
) -> Optional[Dict[str, Any]]:
    market_id = str(fill.get("market_id") or "").strip()
    if market_id:
        return client.get_market_by_id(market_id)
    market_slug = str(fill.get("market_slug") or "").strip()
    if market_slug:
        return client.find_market_by_slug(market_slug)
    return None


def _backfill_market_key(record: Dict[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("market_id") or "").strip(),
        str(record.get("market_slug") or "").strip(),
    )


def build_closed_backfill_index(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "resolved":
            continue
        market_id, market_slug = _backfill_market_key(record)
        if market_id:
            index[f"id:{market_id}"] = record
        if market_slug:
            index[f"slug:{market_slug}"] = record
    return index


def closed_backfill_record_for_fill(
    fill: Dict[str, Any],
    *,
    backfill_index: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    market_id = str(fill.get("market_id") or "").strip()
    if market_id:
        match = backfill_index.get(f"id:{market_id}")
        if match:
            return match
    market_slug = str(fill.get("market_slug") or "").strip()
    if market_slug:
        return backfill_index.get(f"slug:{market_slug}")
    return None


def market_from_closed_backfill_record(record: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = [str(outcome) for outcome in (record.get("outcomes") or [])]
    settled = record.get("settled_probability_by_outcome")
    if not isinstance(settled, dict):
        settled = {}
    outcome_prices = [
        _safe_float(settled.get(outcome)) if outcome in settled else None
        for outcome in outcomes
    ]
    token_ids = [
        str(record.get("winning_token_id") or "") if outcome == record.get("winning_outcome") else ""
        for outcome in outcomes
    ]
    return {
        "id": record.get("market_id"),
        "slug": record.get("market_slug"),
        "question": record.get("question"),
        "closed": True,
        "umaResolutionStatus": "resolved",
        "outcomes": json.dumps(outcomes),
        "outcomePrices": json.dumps(outcome_prices),
        "clobTokenIds": json.dumps(token_ids),
    }


def _audit_state_signature(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": record.get("status"),
        "resolution_source": record.get("resolution_source"),
        "error": record.get("error"),
        "market_id": record.get("market_id"),
        "market_slug": record.get("market_slug"),
        "market_closed": record.get("market_closed"),
        "market_resolution_status": record.get("market_resolution_status"),
        "outcome_index": record.get("outcome_index"),
        "payout": record.get("payout"),
        "winning": record.get("winning"),
        "pnl_cents": record.get("pnl_cents"),
        "backfill_record_id": record.get("backfill_record_id"),
    }


def _filter_unchanged_audit_records(
    records: Iterable[Dict[str, Any]],
    *,
    existing_records: Iterable[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    latest_existing = _latest_audits_by_fill_id(existing_records)
    latest_new_state: Dict[str, Dict[str, Any]] = {}
    filtered: List[Dict[str, Any]] = []
    skipped = 0
    for record in records:
        fill_id = str(record.get("fill_id") or "")
        prior = latest_new_state.get(fill_id) or latest_existing.get(fill_id)
        if prior and _audit_state_signature(prior) == _audit_state_signature(record):
            skipped += 1
            continue
        filtered.append(record)
        latest_new_state[fill_id] = record
    return filtered, skipped


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            json.dump(row, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            count += 1
    return count


def _safe_backup_suffix(value: str) -> str:
    return (
        value.replace("-", "")
        .replace(":", "")
        .replace(".", "")
        .replace("+", "Z")
        .replace("T", "_")
        .replace("Z", "")
    )


def compact_resolved_audit_log(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    write: bool = False,
    backup: bool = True,
    compacted_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove repeated unchanged audit states while preserving fill state transitions."""

    journal_root = Path(journal_dir)
    audit_path = journal_root / "resolved_audits.jsonl"
    records = load_jsonl(audit_path)
    compacted: List[Dict[str, Any]] = []
    latest_kept_state: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    skipped_by_status: Dict[str, int] = {}

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            compacted.append(record)
            continue
        fill_id = str(record.get("fill_id") or f"row:{index}")
        state = _audit_state_signature(record)
        prior_state = latest_kept_state.get(fill_id)
        if prior_state == state:
            skipped += 1
            status = str(record.get("status") or "unknown").strip() or "unknown"
            skipped_by_status[status] = skipped_by_status.get(status, 0) + 1
            continue
        compacted.append(record)
        latest_kept_state[fill_id] = state

    backup_path: Optional[Path] = None
    if write and skipped > 0:
        if backup and audit_path.exists():
            suffix = _safe_backup_suffix(compacted_at or utc_now_iso())
            backup_path = audit_path.with_suffix(f".jsonl.bak.{suffix}")
            backup_path.write_bytes(audit_path.read_bytes())
        tmp_path = audit_path.with_suffix(".jsonl.tmp")
        _write_jsonl(tmp_path, compacted)
        tmp_path.replace(audit_path)

    return {
        "schema_version": RESOLVED_AUDIT_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "audit_path": str(audit_path),
        "compacted_at": compacted_at or utc_now_iso(),
        "write": bool(write),
        "backup": bool(backup),
        "backup_path": str(backup_path) if backup_path else None,
        "original_count": len(records),
        "compacted_count": len(compacted),
        "removed_count": skipped,
        "unique_fill_count": len(
            {
                str(record.get("fill_id") or f"row:{index}")
                for index, record in enumerate(records)
                if isinstance(record, dict)
            }
        ),
        "skipped_by_status": [
            {"status": status, "count": count}
            for status, count in sorted(skipped_by_status.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    }


def audit_paper_fills_resolution(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    client: Optional[PolymarketReadonlyClient] = None,
    recorded_at: Optional[str] = None,
    max_fills: Optional[int] = None,
    include_unresolved: bool = True,
    use_backfill_fallback: bool = True,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    fills = load_jsonl(journal_root / "paper_fills.jsonl")
    if max_fills is not None:
        fills = fills[: max(0, int(max_fills))]
    client = client or PolymarketReadonlyClient()
    recorded_at = recorded_at or utc_now_iso()
    backfill_records = load_jsonl(Path(backfill_dir) / "closed_markets.jsonl") if use_backfill_fallback else []
    backfill_index = build_closed_backfill_index(backfill_records)
    records: List[Dict[str, Any]] = []
    api_resolved_count = 0
    backfill_resolved_count = 0
    backfill_match_count = 0
    for fill in fills:
        try:
            market = resolve_market_for_fill(fill, client=client)
            record = build_resolved_audit_record(fill, market, recorded_at=recorded_at)
        except PolymarketReadonlyError as exc:
            record = build_resolved_audit_record(fill, None, recorded_at=recorded_at, error=str(exc))
        if record.get("status") == "resolved":
            api_resolved_count += 1
        elif use_backfill_fallback:
            backfill_record = closed_backfill_record_for_fill(fill, backfill_index=backfill_index)
            if backfill_record:
                backfill_match_count += 1
                backfill_market = market_from_closed_backfill_record(backfill_record)
                fallback_record = build_resolved_audit_record(
                    fill,
                    backfill_market,
                    recorded_at=recorded_at,
                    resolution_source="closed_backfill",
                )
                fallback_record["backfill_record_id"] = backfill_record.get("record_id")
                fallback_record["backfill_recorded_at"] = backfill_record.get("recorded_at")
                fallback_record["backfill_winning_side"] = backfill_record.get("winning_side")
                fallback_record["backfill_winning_outcome"] = backfill_record.get("winning_outcome")
                fallback_record["backfill_winning_token_id"] = backfill_record.get("winning_token_id")
                if fallback_record.get("status") == "resolved":
                    backfill_resolved_count += 1
                    record = fallback_record
        if record.get("status") == "unresolved" and not include_unresolved:
            continue
        records.append(record)

    audit_path = journal_root / "resolved_audits.jsonl"
    records_to_write, unchanged_skipped_count = _filter_unchanged_audit_records(
        records,
        existing_records=load_jsonl(audit_path),
    )
    written = _append_jsonl(audit_path, records_to_write)
    resolved = [record for record in records if record.get("status") == "resolved"]
    wins = [record for record in resolved if record.get("winning") is True]
    pnl_values = [
        _safe_float(record.get("pnl_cents"))
        for record in resolved
        if _safe_float(record.get("pnl_cents")) is not None
    ]
    return {
        "schema_version": RESOLVED_AUDIT_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "audit_path": str(audit_path),
        "recorded_at": recorded_at,
        "fills_seen": len(fills),
        "backfill_dir": str(backfill_dir),
        "backfill_fallback_enabled": bool(use_backfill_fallback),
        "backfill_records_seen": len(backfill_records),
        "backfill_match_count": backfill_match_count,
        "api_resolved_count": api_resolved_count,
        "backfill_resolved_count": backfill_resolved_count,
        "audit_records_written": written,
        "unchanged_skipped_count": unchanged_skipped_count,
        "resolved_count": len(resolved),
        "unresolved_count": len([record for record in records if record.get("status") == "unresolved"]),
        "error_count": len([record for record in records if record.get("status") == "error"]),
        "win_count": len(wins),
        "win_rate": round(len(wins) / len(resolved), 6) if resolved else None,
        "total_pnl_cents": round(sum(pnl_values), 6) if pnl_values else None,
        "mean_pnl_cents": round(sum(pnl_values) / len(pnl_values), 6) if pnl_values else None,
        "records": records,
    }


def summarize_resolved_audits(journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    records = load_jsonl(journal_root / "resolved_audits.jsonl")
    resolved = [record for record in records if record.get("status") == "resolved"]
    resolution_source_counts: Dict[str, int] = {}
    for record in records:
        source = str(record.get("resolution_source") or "unknown").strip() or "unknown"
        resolution_source_counts[source] = resolution_source_counts.get(source, 0) + 1
    pnl_values = [
        _safe_float(record.get("pnl_cents"))
        for record in resolved
        if _safe_float(record.get("pnl_cents")) is not None
    ]
    wins = [record for record in resolved if record.get("winning") is True]
    return {
        "schema_version": RESOLVED_AUDIT_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "audit_count": len(records),
        "resolved_count": len(resolved),
        "unresolved_count": len([record for record in records if record.get("status") == "unresolved"]),
        "error_count": len([record for record in records if record.get("status") == "error"]),
        "resolution_source_counts": [
            {"resolution_source": source, "count": count}
            for source, count in sorted(
                resolution_source_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ],
        "win_count": len(wins),
        "win_rate": round(len(wins) / len(resolved), 6) if resolved else None,
        "total_pnl_cents": round(sum(pnl_values), 6) if pnl_values else None,
        "mean_pnl_cents": round(sum(pnl_values) / len(pnl_values), 6) if pnl_values else None,
    }


def _latest_audits_by_fill_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        fill_id = str(record.get("fill_id") or f"row:{index}")
        latest[fill_id] = record
    return latest


def _hours_after_end(fill: Dict[str, Any], *, now_iso: str) -> Optional[float]:
    end_dt = _parse_utc_iso(fill.get("end_date"))
    now_dt = _parse_utc_iso(now_iso)
    if end_dt is None or now_dt is None:
        return None
    return round((now_dt - end_dt).total_seconds() / 3600.0, 6)


def _resolution_gap_status(
    fill: Dict[str, Any],
    audit: Dict[str, Any],
    *,
    backfill_match: Optional[Dict[str, Any]],
    now_iso: str,
    settlement_grace_hours: float,
) -> str:
    audit_status = str(audit.get("status") or "").strip().lower()
    if audit_status == "resolved":
        return "resolved"
    if audit_status == "error":
        return "audit_error"
    if audit_status == "missing_outcome_match":
        return "missing_outcome_match"
    if audit_status == "missing_payout":
        return "missing_payout"
    if backfill_match is not None:
        return "backfill_match_not_resolved"
    hours_after_end = _hours_after_end(fill, now_iso=now_iso)
    if hours_after_end is None:
        return "missing_end_date"
    if hours_after_end < 0:
        return "not_due"
    if hours_after_end <= float(settlement_grace_hours):
        return "within_settlement_grace"
    if audit_status == "missing_market":
        return "overdue_missing_market"
    if audit.get("market_closed") is False:
        return "overdue_api_market_unclosed"
    return "overdue_no_backfill_match"


def _count_by_field(rows: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _resolution_gap_action(status: str) -> str:
    if status == "resolved":
        return "include_in_resolved_evidence"
    if status == "not_due":
        return "wait_until_market_end"
    if status == "within_settlement_grace":
        return "refresh_after_settlement_grace"
    if status in {"overdue_api_market_unclosed", "overdue_missing_market", "overdue_no_backfill_match"}:
        return "expand_targeted_closed_backfill_or_recheck_polymarket"
    if status in {"missing_outcome_match", "missing_payout", "backfill_match_not_resolved"}:
        return "inspect_resolution_mapping"
    if status == "missing_end_date":
        return "repair_fill_end_date"
    return "inspect_audit_error"


def build_resolved_gap_report(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    generated_at: Optional[str] = None,
    settlement_grace_hours: float = 24.0,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    journal_root = Path(journal_dir)
    backfill_root = Path(backfill_dir)
    fills = [row for row in load_jsonl(journal_root / "paper_fills.jsonl") if isinstance(row, dict)]
    latest_audits = _latest_audits_by_fill_id(load_jsonl(journal_root / "resolved_audits.jsonl"))
    backfill_records = load_jsonl(backfill_root / "closed_markets.jsonl")
    backfill_index = build_closed_backfill_index(backfill_records)
    rows: List[Dict[str, Any]] = []
    for fill in fills:
        audit = latest_audits.get(str(fill.get("fill_id") or "")) or {}
        backfill_match = closed_backfill_record_for_fill(fill, backfill_index=backfill_index)
        status = _resolution_gap_status(
            fill,
            audit,
            backfill_match=backfill_match,
            now_iso=generated_at,
            settlement_grace_hours=settlement_grace_hours,
        )
        rows.append(
            {
                "fill_id": fill.get("fill_id"),
                "market_id": fill.get("market_id"),
                "market_slug": fill.get("market_slug"),
                "city": fill.get("city"),
                "side": fill.get("side"),
                "signal_bucket": fill.get("signal_bucket"),
                "counts_for_live_gate": fill.get("counts_for_live_gate", True) is not False,
                "entry_price": _safe_float(fill.get("entry_price")),
                "recorded_at": fill.get("recorded_at"),
                "end_date": fill.get("end_date"),
                "hours_after_end": _hours_after_end(fill, now_iso=generated_at),
                "latest_audit_status": audit.get("status") or "missing_audit",
                "latest_resolution_source": audit.get("resolution_source") or "missing_audit",
                "market_closed": audit.get("market_closed"),
                "backfill_match": backfill_match is not None,
                "backfill_record_id": (backfill_match or {}).get("record_id") if backfill_match else None,
                "gap_status": status,
                "next_action": _resolution_gap_action(status),
            }
        )
    live_gate_rows = [
        row
        for row in rows
        if row.get("counts_for_live_gate") is True
        and str(row.get("signal_bucket") or "") != "quarantine"
    ]
    overdue_rows = [
        row
        for row in rows
        if str(row.get("gap_status") or "").startswith("overdue_")
    ]
    return {
        "schema_version": RESOLVED_GAP_SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "journal_dir": str(journal_root),
        "backfill_dir": str(backfill_root),
        "settlement_grace_hours": float(settlement_grace_hours),
        "fill_count": len(fills),
        "live_gate_fill_count": len(live_gate_rows),
        "backfill_record_count": len(backfill_records),
        "gap_status_counts": _count_by_field(rows, "gap_status"),
        "next_action_counts": _count_by_field(rows, "next_action"),
        "live_gate_gap_status_counts": _count_by_field(live_gate_rows, "gap_status"),
        "overdue_count": len(overdue_rows),
        "rows": rows,
        "hard_conclusion": (
            "resolved_gap_contains_overdue_backfill_work"
            if overdue_rows
            else (
                "resolved_gap_wait_for_settlement"
                if any(row.get("gap_status") in {"not_due", "within_settlement_grace"} for row in rows)
                else "resolved_gap_no_actionable_rows"
            )
        ),
    }


def dump_summary(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
