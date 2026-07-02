from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_orderbook_archive import (
    DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    build_orderbook_snapshot_records,
)
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements
from src.trading.weather_historical_evidence import historical_evidence_from_replay_fills
from src.trading.weather_paper_journal import _safe_float, load_jsonl, utc_now_iso
from src.trading.weather_replay import replay_taker_candidates
from src.trading.weather_strict_gate_replay import resolved_outcomes_from_audits_and_backfill


CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION = "polyweather_weather_closed_historical_replay.v1"


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


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        value = _text(record.get(field)) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _market_key(row: Dict[str, Any]) -> str:
    return _text(row.get("market_id") or row.get("market_slug"))


def _record_end_time(record: Dict[str, Any]) -> Optional[datetime]:
    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    for value in (
        spec.get("end_time"),
        record.get("end_time"),
        record.get("endTime"),
        record.get("end_date"),
        record.get("endDate"),
    ):
        parsed = _parse_utc(value)
        if parsed is not None:
            return parsed
    return None


def _snapshot_time(row: Dict[str, Any]) -> Optional[datetime]:
    for field in ("snapshot_recorded_at", "payload_generated_at", "recorded_at", "available_at", "generated_at"):
        parsed = _parse_utc(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _archive_time(row: Dict[str, Any]) -> Optional[datetime]:
    for field in ("recorded_at", "available_at", "generated_at", "orderbook_recorded_at"):
        parsed = _parse_utc(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _orderbook_ready(row: Dict[str, Any]) -> bool:
    order_book = row.get("order_book") if isinstance(row.get("order_book"), dict) else {}
    asks = (
        order_book.get("ask_ladder")
        or order_book.get("asks")
        or row.get("ask_ladder")
        or row.get("asks")
    )
    return isinstance(asks, list) and bool(asks)


def _yes_token_id(record: Dict[str, Any]) -> Optional[str]:
    token_map = record.get("token_id_by_outcome") if isinstance(record.get("token_id_by_outcome"), dict) else {}
    token_id = _text(token_map.get("Yes") or token_map.get("yes"))
    if token_id:
        return token_id
    if _text(record.get("winning_outcome")).lower() == "yes":
        return _text(record.get("winning_token_id")) or None
    return None


def _load_closed_snapshot_rows(backfill_dir: str | Path) -> List[Dict[str, Any]]:
    snapshot_dir = Path(backfill_dir) / "snapshots"
    rows: List[Dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = raw.get("payload") if isinstance(raw, dict) and isinstance(raw.get("payload"), dict) else raw
        if not isinstance(payload, dict):
            continue
        snapshot_recorded_at = raw.get("recorded_at") or payload.get("generated_at")
        source_snapshot_id = payload.get("snapshot_id") or path.stem
        for row in payload.get("rows") or []:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            enriched["snapshot_recorded_at"] = snapshot_recorded_at
            enriched["payload_generated_at"] = payload.get("generated_at")
            enriched["source_snapshot_id"] = source_snapshot_id
            enriched["snapshot_path"] = str(path)
            rows.append(enriched)
    return rows


def load_closed_snapshot_rows(backfill_dir: str | Path) -> List[Dict[str, Any]]:
    return _load_closed_snapshot_rows(backfill_dir)


def _first_yes_snapshot_by_market(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    selected_time: Dict[str, datetime] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = _text(row.get("side") or row.get("outcome")).lower()
        if side not in {"yes", "y"}:
            continue
        key = _market_key(row)
        snapshot_time = _snapshot_time(row)
        if not key or snapshot_time is None:
            continue
        if key not in selected_time or snapshot_time > selected_time[key]:
            selected[key] = row
            selected_time[key] = snapshot_time
    return selected


def _candidate_from_snapshot(row: Dict[str, Any], *, snapshot_time: datetime) -> Dict[str, Any]:
    market_probability = _safe_float(row.get("market_probability") if row.get("market_probability") is not None else row.get("price"))
    ask = _safe_float(row.get("best_ask") or row.get("ask"))
    market_bucket = row.get("market_bucket") if isinstance(row.get("market_bucket"), dict) else {}
    return {
        "schema_version": CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate_excluded": True,
        "diagnostic_only": True,
        "source": "closed_snapshot_preresolution",
        "id": f"closed-hist-{_text(row.get('market_id') or row.get('market_slug'))}",
        "available_at": _iso(snapshot_time),
        "generated_at": _iso(snapshot_time),
        "market_id": row.get("market_id"),
        "market_slug": row.get("market_slug"),
        "event_slug": row.get("event_slug"),
        "token_id": row.get("token_id"),
        "side": row.get("side") or "yes",
        "bucket_type": market_bucket.get("bucket_type") or row.get("bucket_type"),
        "strategy_id": "closed_historical_market_implied_baseline",
        "queue_name": "closed_historical_replay",
        "model_probability": market_probability,
        "p_model": market_probability,
        "p_lcb": market_probability,
        "q_effective": ask,
        "ev_safe": (market_probability - ask) if market_probability is not None and ask is not None else None,
    }


def _candidate_from_archived_orderbook(
    row: Dict[str, Any],
    *,
    archive_time: datetime,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    best_bid = _safe_float(row.get("best_bid"))
    best_ask = _safe_float(row.get("best_ask"))
    if best_bid is not None and best_ask is not None:
        probability = round((float(best_bid) + float(best_ask)) / 2.0, 8)
        probability_source = "archived_orderbook_midpoint"
    else:
        probability = best_ask
        probability_source = "archived_orderbook_best_ask"
    return {
        "schema_version": CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate_excluded": True,
        "diagnostic_only": True,
        "source": "preresolution_orderbook_archive",
        "id": f"archive-hist-{_text(row.get('snapshot_id') or row.get('token_id') or row.get('market_slug'))}",
        "available_at": _iso(archive_time),
        "generated_at": _iso(archive_time),
        "market_id": row.get("market_id") or record.get("market_id"),
        "market_slug": row.get("market_slug") or record.get("market_slug"),
        "event_slug": row.get("event_slug") or record.get("event_slug"),
        "token_id": row.get("token_id"),
        "side": row.get("side") or "yes",
        "bucket_type": row.get("bucket_type") or _text(record.get("bucket_type")),
        "strategy_id": "preresolution_market_implied_baseline",
        "queue_name": "preresolution_orderbook_replay",
        "model_probability": probability,
        "p_model": probability,
        "p_lcb": probability,
        "prediction_probability_source": probability_source,
        "q_effective": best_ask,
        "ev_safe": (float(probability) - float(best_ask)) if probability is not None and best_ask is not None else None,
    }


def build_closed_historical_replay_inputs(
    *,
    closed_records: Iterable[Dict[str, Any]],
    snapshot_rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    records = [record for record in closed_records if isinstance(record, dict) and record.get("status") == "resolved"]
    snapshots = [row for row in snapshot_rows if isinstance(row, dict)]
    snapshot_by_market = _first_yes_snapshot_by_market(snapshots)
    candidates: List[Dict[str, Any]] = []
    orderbook_rows: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    for record in records:
        key = _market_key(record)
        snapshot = snapshot_by_market.get(key)
        if snapshot is None:
            gaps.append({"market_slug": record.get("market_slug"), "market_id": record.get("market_id"), "gap_reason": "missing_snapshot_row"})
            continue
        end_time = _record_end_time(record) or _parse_utc(snapshot.get("end_date") or snapshot.get("endDate"))
        snapshot_time = _snapshot_time(snapshot)
        if end_time is None:
            gaps.append({"market_slug": record.get("market_slug"), "market_id": record.get("market_id"), "gap_reason": "missing_market_end_time"})
            continue
        if snapshot_time is None:
            gaps.append({"market_slug": record.get("market_slug"), "market_id": record.get("market_id"), "gap_reason": "missing_snapshot_time"})
            continue
        if snapshot_time > end_time:
            gaps.append(
                {
                    "market_slug": record.get("market_slug"),
                    "market_id": record.get("market_id"),
                    "gap_reason": "post_resolution_snapshot",
                    "snapshot_time": _iso(snapshot_time),
                    "market_end_time": _iso(end_time),
                }
            )
            continue
        if snapshot.get("closed") is True or snapshot.get("accepting_orders") is False or snapshot.get("tradable") is False:
            gaps.append(
                {
                    "market_slug": record.get("market_slug"),
                    "market_id": record.get("market_id"),
                    "gap_reason": "closed_or_not_tradable_snapshot",
                    "snapshot_time": _iso(snapshot_time),
                    "market_end_time": _iso(end_time),
                }
            )
            continue
        if not _text(snapshot.get("token_id")):
            gaps.append({"market_slug": record.get("market_slug"), "market_id": record.get("market_id"), "gap_reason": "missing_token_id"})
            continue
        if not _orderbook_ready(snapshot):
            gaps.append(
                {
                    "market_slug": record.get("market_slug"),
                    "market_id": record.get("market_id"),
                    "gap_reason": "missing_orderbook_depth",
                    "snapshot_time": _iso(snapshot_time),
                    "market_end_time": _iso(end_time),
                }
            )
            continue
        row = dict(snapshot)
        row.setdefault("market_id", record.get("market_id"))
        row.setdefault("market_slug", record.get("market_slug"))
        row["recorded_at"] = _iso(snapshot_time)
        candidates.append(_candidate_from_snapshot(row, snapshot_time=snapshot_time))
        orderbook_rows.extend(
            build_orderbook_snapshot_records(
                [row],
                recorded_at=_iso(snapshot_time),
                source_snapshot_id=row.get("source_snapshot_id"),
                source="closed_historical_snapshot",
            )
        )
    return {
        "schema_version": CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "closed_record_count": len(records),
        "snapshot_row_count": len(snapshots),
        "candidate_count": len(candidates),
        "orderbook_snapshot_count": len(orderbook_rows),
        "gap_count": len(gaps),
        "gaps_by_reason": _count_by(gaps, "gap_reason", key_name="reason"),
        "candidates": candidates,
        "orderbook_snapshots": orderbook_rows,
        "gap_samples": gaps[:20],
    }


def _hard_conclusion(inputs: Dict[str, Any], replay: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    if int(inputs.get("closed_record_count") or 0) <= 0:
        return "closed_historical_replay_no_closed_records"
    if int(inputs.get("snapshot_row_count") or 0) <= 0:
        return "closed_historical_replay_no_snapshot_rows"
    if int(inputs.get("candidate_count") or 0) <= 0:
        reasons = inputs.get("gaps_by_reason") or []
        if reasons:
            top = reasons[0].get("reason")
            if top == "post_resolution_snapshot":
                return "closed_historical_replay_no_preresolution_snapshots"
            if top == "missing_orderbook_depth":
                return "closed_historical_replay_missing_orderbook_depth"
        return "closed_historical_replay_no_candidates"
    if int(replay.get("missing_resolution_count") or 0) > 0:
        return "closed_historical_replay_missing_resolved_outcomes"
    if int(evidence.get("supplement_count") or 0) <= 0:
        return "closed_historical_replay_no_calibration_evidence"
    return "closed_historical_replay_ready_for_settlement_calibration"


def build_closed_historical_replay_report(
    *,
    closed_records: Iterable[Dict[str, Any]],
    snapshot_rows: Iterable[Dict[str, Any]],
    replay_time: Optional[str] = None,
    size: float = 1.0,
) -> Dict[str, Any]:
    replay_time = replay_time or utc_now_iso()
    input_report = build_closed_historical_replay_inputs(
        closed_records=closed_records,
        snapshot_rows=snapshot_rows,
    )
    closed_rows = [record for record in closed_records if isinstance(record, dict)]
    resolved_outcomes = resolved_outcomes_from_audits_and_backfill(
        audit_records=[],
        backfill_records=closed_rows,
    )
    replay = replay_taker_candidates(
        candidates=input_report["candidates"],
        orderbook_snapshots=input_report["orderbook_snapshots"],
        resolved_outcomes=resolved_outcomes,
        replay_time=replay_time,
        size=size,
    )
    evidence = historical_evidence_from_replay_fills(
        replay.get("fills") or [],
        source="closed_historical_replay",
    )
    report = {
        "schema_version": CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "replay_time": replay_time,
        "size": float(size),
        "input_summary": {
            key: input_report.get(key)
            for key in (
                "closed_record_count",
                "snapshot_row_count",
                "candidate_count",
                "orderbook_snapshot_count",
                "gap_count",
                "gaps_by_reason",
                "gap_samples",
            )
        },
        "resolved_outcome_count": len(resolved_outcomes),
        "replay": replay,
        "historical_evidence": evidence,
        "candidates": input_report["candidates"],
        "orderbook_snapshots": input_report["orderbook_snapshots"],
    }
    report["hard_conclusion"] = _hard_conclusion(input_report, replay, evidence)
    return report


def build_closed_historical_replay_report_from_dir(
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    *,
    official_value_supplements_path: Optional[str | Path] = None,
    replay_time: Optional[str] = None,
    size: float = 1.0,
) -> Dict[str, Any]:
    return build_closed_historical_replay_report(
        closed_records=load_closed_backfill_records_with_snapshot_supplements(
            backfill_dir,
            official_value_supplements_path=official_value_supplements_path,
        ),
        snapshot_rows=load_closed_snapshot_rows(backfill_dir),
        replay_time=replay_time,
        size=size,
    )


def _archive_rows_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = _text(row.get("side") or row.get("outcome")).lower()
        if side not in {"", "yes", "y"}:
            continue
        token_id = _text(row.get("token_id"))
        if not token_id:
            continue
        grouped.setdefault(token_id, []).append(row)
    for token_rows in grouped.values():
        token_rows.sort(key=lambda item: _archive_time(item) or datetime.min.replace(tzinfo=timezone.utc))
    return grouped


def build_preresolution_orderbook_replay_inputs(
    *,
    closed_records: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    records = [record for record in closed_records if isinstance(record, dict) and record.get("status") == "resolved"]
    archive_rows = [row for row in orderbook_snapshots if isinstance(row, dict)]
    rows_by_token = _archive_rows_by_token(archive_rows)
    candidates: List[Dict[str, Any]] = []
    selected_orderbooks: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    for record in records:
        end_time = _record_end_time(record)
        token_id = _yes_token_id(record)
        base_gap = {
            "market_slug": record.get("market_slug"),
            "market_id": record.get("market_id"),
            "token_id": token_id,
        }
        if end_time is None:
            gaps.append({**base_gap, "gap_reason": "missing_market_end_time"})
            continue
        if not token_id:
            gaps.append({**base_gap, "gap_reason": "missing_yes_token_id"})
            continue
        token_rows = rows_by_token.get(token_id) or []
        if not token_rows:
            gaps.append({**base_gap, "gap_reason": "missing_archived_orderbook"})
            continue
        visible_rows: List[Tuple[datetime, Dict[str, Any]]] = []
        future_seen = False
        missing_time_seen = False
        for row in token_rows:
            recorded_at = _archive_time(row)
            if recorded_at is None:
                missing_time_seen = True
                continue
            if recorded_at > end_time:
                future_seen = True
                continue
            visible_rows.append((recorded_at, row))
        if not visible_rows:
            reason = "post_resolution_archive_snapshot" if future_seen else "missing_archive_timestamp"
            if missing_time_seen and not future_seen:
                reason = "missing_archive_timestamp"
            gaps.append({**base_gap, "gap_reason": reason, "market_end_time": _iso(end_time)})
            continue
        archive_time, selected = max(visible_rows, key=lambda item: item[0])
        if not _orderbook_ready(selected):
            gaps.append(
                {
                    **base_gap,
                    "gap_reason": "missing_orderbook_depth",
                    "archive_time": _iso(archive_time),
                    "market_end_time": _iso(end_time),
                }
            )
            continue
        best_ask = _safe_float(selected.get("best_ask"))
        if best_ask is None:
            gaps.append(
                {
                    **base_gap,
                    "gap_reason": "missing_best_ask",
                    "archive_time": _iso(archive_time),
                    "market_end_time": _iso(end_time),
                }
            )
            continue
        candidates.append(
            _candidate_from_archived_orderbook(
                selected,
                archive_time=archive_time,
                record=record,
            )
        )
        selected_orderbooks.append(selected)
    return {
        "schema_version": CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "closed_record_count": len(records),
        "archived_orderbook_count": len(archive_rows),
        "candidate_count": len(candidates),
        "orderbook_snapshot_count": len(selected_orderbooks),
        "gap_count": len(gaps),
        "gaps_by_reason": _count_by(gaps, "gap_reason", key_name="reason"),
        "candidates": candidates,
        "orderbook_snapshots": selected_orderbooks,
        "gap_samples": gaps[:20],
    }


def _archive_hard_conclusion(inputs: Dict[str, Any], replay: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    if int(inputs.get("closed_record_count") or 0) <= 0:
        return "preresolution_orderbook_replay_no_closed_records"
    if int(inputs.get("archived_orderbook_count") or 0) <= 0:
        return "preresolution_orderbook_replay_no_archive"
    if int(inputs.get("candidate_count") or 0) <= 0:
        reasons = inputs.get("gaps_by_reason") or []
        top = reasons[0].get("reason") if reasons else None
        if top == "missing_archived_orderbook":
            return "preresolution_orderbook_replay_missing_archive_coverage"
        if top == "post_resolution_archive_snapshot":
            return "preresolution_orderbook_replay_no_preresolution_archive"
        if top == "missing_orderbook_depth":
            return "preresolution_orderbook_replay_missing_orderbook_depth"
        return "preresolution_orderbook_replay_no_candidates"
    if int(replay.get("missing_resolution_count") or 0) > 0:
        return "preresolution_orderbook_replay_missing_resolved_outcomes"
    if int(evidence.get("supplement_count") or 0) <= 0:
        return "preresolution_orderbook_replay_no_calibration_evidence"
    return "preresolution_orderbook_replay_ready_for_settlement_calibration"


def build_preresolution_orderbook_replay_report(
    *,
    closed_records: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    replay_time: Optional[str] = None,
    size: float = 1.0,
) -> Dict[str, Any]:
    replay_time = replay_time or utc_now_iso()
    input_report = build_preresolution_orderbook_replay_inputs(
        closed_records=closed_records,
        orderbook_snapshots=orderbook_snapshots,
    )
    closed_rows = [record for record in closed_records if isinstance(record, dict)]
    resolved_outcomes = resolved_outcomes_from_audits_and_backfill(
        audit_records=[],
        backfill_records=closed_rows,
    )
    replay = replay_taker_candidates(
        candidates=input_report["candidates"],
        orderbook_snapshots=input_report["orderbook_snapshots"],
        resolved_outcomes=resolved_outcomes,
        replay_time=replay_time,
        size=size,
    )
    evidence = historical_evidence_from_replay_fills(
        replay.get("fills") or [],
        source="preresolution_orderbook_replay",
    )
    report = {
        "schema_version": CLOSED_HISTORICAL_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "replay_time": replay_time,
        "size": float(size),
        "input_summary": {
            key: input_report.get(key)
            for key in (
                "closed_record_count",
                "archived_orderbook_count",
                "candidate_count",
                "orderbook_snapshot_count",
                "gap_count",
                "gaps_by_reason",
                "gap_samples",
            )
        },
        "resolved_outcome_count": len(resolved_outcomes),
        "replay": replay,
        "historical_evidence": evidence,
        "candidates": input_report["candidates"],
        "orderbook_snapshots": input_report["orderbook_snapshots"],
    }
    report["hard_conclusion"] = _archive_hard_conclusion(input_report, replay, evidence)
    return report


def build_preresolution_orderbook_replay_report_from_dirs(
    *,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    orderbook_archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    official_value_supplements_path: Optional[str | Path] = None,
    replay_time: Optional[str] = None,
    size: float = 1.0,
) -> Dict[str, Any]:
    return build_preresolution_orderbook_replay_report(
        closed_records=load_closed_backfill_records_with_snapshot_supplements(
            backfill_dir,
            official_value_supplements_path=official_value_supplements_path,
        ),
        orderbook_snapshots=load_jsonl(Path(orderbook_archive_dir) / "orderbook_snapshots.jsonl"),
        replay_time=replay_time,
        size=size,
    )
