#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.weather_archived_orderbook_due_refresh_report import (  # noqa: E402
    CONFIRM_TOKEN,
    build_due_refresh_report,
)
from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR  # noqa: E402
from src.trading.weather_closed_backfill import (  # noqa: E402
    DEFAULT_BACKFILL_DIR,
    run_targeted_closed_weather_backfill_from_market_slugs,
)
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements  # noqa: E402
from src.trading.weather_historical_evidence import historical_evidence_from_replay_report  # noqa: E402
from src.trading.weather_live_evidence_bundle import (  # noqa: E402
    _archived_yes_token_ids,
    _closed_records_with_archived_overlap,
    build_live_evidence_bundle_report,
    filter_records_by_settlement_scope,
    settlement_source,
    settlement_station_code,
)
from src.trading.weather_live_readiness import build_live_readiness_report  # noqa: E402
from src.trading.weather_orderbook_archive_coverage import build_orderbook_closed_token_coverage_report  # noqa: E402
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR, load_jsonl  # noqa: E402
from src.trading.weather_settlement_calibration import build_settlement_calibration_report  # noqa: E402
from src.trading.weather_settlement_truth_audit import build_settlement_truth_audit_report  # noqa: E402
from src.trading.weather_strict_gate_queue import default_strict_gate_queue_dir  # noqa: E402
from src.trading.weather_strict_gate_replay import (  # noqa: E402
    build_strict_gate_replay_report,
    resolved_outcomes_from_audits_and_backfill,
)
from src.weather.official_value_backfill import (  # noqa: E402
    apply_official_value_supplements,
    build_official_observation_request_plan,
    build_official_value_backfill_report,
)


SCHEMA_VERSION = "polyweather_weather_due_evidence_pipeline.v1"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows if isinstance(row, dict)),
        encoding="utf-8",
    )


def _load_orderbooks(orderbook_archive_dir: str | Path) -> list[Dict[str, Any]]:
    return load_jsonl(Path(orderbook_archive_dir) / "orderbook_snapshots.jsonl")


def _overlap_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "matched_closed_archived_token_count": report.get("matched_closed_archived_token_count"),
        "pending_closed_backfill_due_token_count": report.get("pending_closed_backfill_due_token_count"),
        "closed_backfill_due_token_count": report.get("closed_backfill_due_token_count"),
        "pending_awaiting_observation_window_end_token_count": report.get(
            "pending_awaiting_observation_window_end_token_count"
        ),
        "pending_awaiting_settlement_due_time_token_count": report.get(
            "pending_awaiting_settlement_due_time_token_count"
        ),
        "wrong_due_prevented_count": report.get("wrong_due_prevented_count"),
    }


def _list_arg(values: Optional[Iterable[str]], *, upper: bool = False) -> list[str]:
    rows = []
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        rows.append(text.upper() if upper else text.lower())
    return rows


def _market_query(record: Dict[str, Any]) -> str:
    return str(record.get("market_slug") or record.get("market_id") or "").strip()


def _filter_records(
    records: Iterable[Dict[str, Any]],
    *,
    include_settlement_sources: Optional[Iterable[str]] = None,
    exclude_settlement_sources: Optional[Iterable[str]] = None,
    include_station_codes: Optional[Iterable[str]] = None,
    exclude_station_codes: Optional[Iterable[str]] = None,
) -> list[Dict[str, Any]]:
    return filter_records_by_settlement_scope(
        [row for row in records if isinstance(row, dict)],
        include_settlement_sources=include_settlement_sources,
        exclude_settlement_sources=exclude_settlement_sources,
        include_station_codes=include_station_codes,
        exclude_station_codes=exclude_station_codes,
    )


def _supported_official_source(source: str) -> bool:
    return str(source or "").strip().lower() in {"metar", "wunderground"}


def _group_id(*, settlement_due_time: str, source: str, supported: bool) -> str:
    source_text = str(source or "unknown").strip().lower() or "unknown"
    if not supported:
        return f"unsupported_{source_text}_adapter"
    compact_due = (
        str(settlement_due_time or "unknown_due")
        .replace("-", "_")
        .replace(":", "")
        .replace(".", "")
        .replace("Z", "Z")
    )
    return f"due_at_{compact_due}_supported_{source_text}"


def _execution_groups_from_records(records: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    grouped: Dict[tuple[str, str, bool], Dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        source = settlement_source(record)
        station = settlement_station_code(record)
        due_time = str(record.get("settlement_due_time") or "").strip()
        supported = _supported_official_source(source)
        key = (due_time, source, supported)
        row = grouped.setdefault(
            key,
            {
                "group_id": _group_id(
                    settlement_due_time=due_time,
                    source=source,
                    supported=supported,
                ),
                "token_count": 0,
                "market_count": 0,
                "station_codes": [],
                "station_code": None,
                "settlement_source": source,
                "settlement_due_time": due_time or None,
                "supported_official_source": supported,
                "market_slug_samples": [],
                "pending_status_counts": {},
            },
        )
        row["token_count"] += 1
        row["market_count"] += 1
        if station and station not in row["station_codes"]:
            row["station_codes"].append(station)
        slug = _market_query(record)
        if slug and len(row["market_slug_samples"]) < 5:
            row["market_slug_samples"].append(slug)
        status = str(record.get("pending_closed_backfill_status") or "unknown")
        row["pending_status_counts"][status] = int(row["pending_status_counts"].get(status) or 0) + 1

    groups = []
    for row in grouped.values():
        row["station_codes"] = sorted(row["station_codes"])
        row["station_code"] = "+".join(row["station_codes"]) if row["station_codes"] else None
        row["pending_status_counts"] = [
            {"status": status, "count": count}
            for status, count in sorted(row["pending_status_counts"].items(), key=lambda pair: (-pair[1], pair[0]))
        ]
        row["next_action"] = (
            "skip_until_official_source_adapter_exists"
            if row["supported_official_source"] is False
            else "execute_targeted_closed_backfill_when_due"
        )
        groups.append(row)
    return sorted(
        groups,
        key=lambda row: (
            row.get("settlement_due_time") or "",
            row.get("settlement_source") or "",
            row.get("station_code") or "",
        ),
    )


def _records_from_followup(plan_report: Dict[str, Any], *fields: str) -> list[Dict[str, Any]]:
    followup = (
        plan_report.get("closed_backfill_followup_plan")
        if isinstance(plan_report.get("closed_backfill_followup_plan"), dict)
        else {}
    )
    rows = []
    for field in fields:
        rows.extend(row for row in followup.get(field) or [] if isinstance(row, dict))
    return rows


def _token_ids(records: Iterable[Dict[str, Any]]) -> set[str]:
    return {
        str(row.get("token_id") or "").strip()
        for row in records
        if isinstance(row, dict) and str(row.get("token_id") or "").strip()
    }


def _filter_queue_by_tokens(records: Iterable[Dict[str, Any]], token_ids: set[str]) -> list[Dict[str, Any]]:
    if not token_ids:
        return [row for row in records if isinstance(row, dict)]
    return [
        row
        for row in records
        if isinstance(row, dict)
        and (not str(row.get("token_id") or "").strip() or str(row.get("token_id") or "").strip() in token_ids)
    ]


def _plus_hours_iso(value: Any, hours: int) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _unresolved_token_rows(records: Iterable[Dict[str, Any]], *, limit: int = 100) -> list[Dict[str, Any]]:
    rows = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        token_id = str(record.get("token_id") or "").strip()
        market_slug = _market_query(record)
        key = (token_id, market_slug)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "token_id": token_id or None,
                "market_slug": market_slug or None,
                "station_code": settlement_station_code(record) or None,
                "settlement_source": settlement_source(record) or None,
                "settlement_due_time": record.get("settlement_due_time"),
                "target_date": record.get("target_date")
                or (record.get("settlement_spec") or {}).get("target_date")
                if isinstance(record.get("settlement_spec"), dict)
                else record.get("target_date"),
            }
        )
        if len(rows) >= max(0, int(limit)):
            break
    return rows


def _alpha_conclusion(strict_report: Dict[str, Any], calibration_report: Dict[str, Any]) -> str:
    replay = strict_report.get("replay") if isinstance(strict_report.get("replay"), dict) else {}
    resolved_fill_count = int(strict_report.get("resolved_fill_count") or 0)
    resolved_pnl_cents = replay.get("resolved_pnl_cents")
    probability_score_count = int(calibration_report.get("probability_score_sample_count") or 0)
    resolved_pnl_sample_count = int(calibration_report.get("resolved_pnl_sample_count") or 0)
    if resolved_fill_count <= 0:
        return "inconclusive_waiting_for_resolution_or_backfill"
    try:
        pnl = float(resolved_pnl_cents)
    except (TypeError, ValueError):
        return "inconclusive_waiting_for_resolution_or_backfill"
    if pnl < 0.0:
        return "alpha_failed_negative_resolved_pnl"
    if pnl > 0.0 and probability_score_count <= 0:
        return "pnl_positive_but_uncalibrated"
    if (
        pnl > 0.0
        and probability_score_count > 0
        and resolved_pnl_sample_count > 0
        and calibration_report.get("hard_conclusion") == "settlement_calibration_ready_diagnostic_only"
    ):
        return "alpha_candidate_paper_only_review"
    if pnl > 0.0:
        return "pnl_positive_but_uncalibrated"
    return "inconclusive_zero_resolved_pnl"


def _readiness_compact(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "paper_only": report.get("paper_only"),
        "live_gate": report.get("live_gate"),
        "evidence_gate_passed": report.get("evidence_gate_passed"),
        "live_order_path_available": report.get("live_order_path_available"),
        "hard_gate_failures": report.get("hard_gate_failures"),
        "hard_conclusion": report.get("hard_conclusion"),
    }


def build_due_evidence_pipeline_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    orderbook_archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    generated_at: Optional[str] = None,
    replay_time: Optional[str] = None,
    execute_closed_backfill: bool = False,
    confirm: Optional[str] = None,
    size: float = 1.0,
    max_samples: int = 1000,
    include_settlement_sources: Optional[Iterable[str]] = None,
    exclude_settlement_sources: Optional[Iterable[str]] = None,
    include_station_codes: Optional[Iterable[str]] = None,
    exclude_station_codes: Optional[Iterable[str]] = None,
    allow_partial_official_truth: bool = False,
    fetch_external_official_values: bool = False,
) -> Dict[str, Any]:
    replay_time = replay_time or generated_at
    due_dry_run = build_due_refresh_report(
        backfill_dir=backfill_dir,
        orderbook_archive_dir=orderbook_archive_dir,
        generated_at=generated_at,
        replay_time=replay_time,
        size=size,
        max_samples=max_samples,
        execute=False,
    )
    plan_report = due_dry_run.get("plan_report") if isinstance(due_dry_run.get("plan_report"), dict) else {}
    followup = (
        plan_report.get("closed_backfill_followup_plan")
        if isinstance(plan_report.get("closed_backfill_followup_plan"), dict)
        else {}
    )
    all_due_records = _records_from_followup(plan_report, "requests")
    all_wait_records = _records_from_followup(
        plan_report,
        "awaiting_observation_window_end_samples",
        "awaiting_settlement_due_time_samples",
    )
    all_scope_records = [*all_due_records, *all_wait_records]
    execution_groups = _execution_groups_from_records(all_scope_records)
    filtered_due_records = _filter_records(
        all_due_records,
        include_settlement_sources=include_settlement_sources,
        exclude_settlement_sources=exclude_settlement_sources,
        include_station_codes=include_station_codes,
        exclude_station_codes=exclude_station_codes,
    )
    filtered_wait_records = _filter_records(
        all_wait_records,
        include_settlement_sources=include_settlement_sources,
        exclude_settlement_sources=exclude_settlement_sources,
        include_station_codes=include_station_codes,
        exclude_station_codes=exclude_station_codes,
    )
    filtered_scope_records = [*filtered_due_records, *filtered_wait_records]
    filtered_execution_groups = _execution_groups_from_records(filtered_scope_records)
    due_count = len(filtered_due_records)
    due_status = "closed_backfill_due" if due_count > 0 else "waiting_for_settlement_due_time"
    if not due_count and filtered_wait_records:
        due_status = "waiting_for_observation_window_end"

    due_execute = None
    targeted_backfill_result = None
    if execute_closed_backfill:
        if confirm != CONFIRM_TOKEN:
            due_execute = {
                "execution": {
                    "paper_only": True,
                    "status": "confirm_missing",
                    "confirm_required": CONFIRM_TOKEN,
                }
            }
            due_status = "confirm_missing"
        elif due_count <= 0:
            due_execute = build_due_refresh_report(
                backfill_dir=backfill_dir,
                orderbook_archive_dir=orderbook_archive_dir,
                generated_at=generated_at,
                replay_time=replay_time,
                size=size,
                max_samples=max_samples,
                execute=True,
                confirm=confirm,
            )
            execution = due_execute.get("execution") if isinstance(due_execute.get("execution"), dict) else {}
            due_status = str(execution.get("status") or due_status)
        else:
            market_queries = []
            seen_queries = set()
            for record in filtered_due_records:
                query = _market_query(record)
                if query and query not in seen_queries:
                    market_queries.append(query)
                    seen_queries.add(query)
            targeted_backfill_result = run_targeted_closed_weather_backfill_from_market_slugs(
                market_slugs=market_queries,
                backfill_dir=backfill_dir,
            )
            due_status = "targeted_closed_backfill_executed"
            after_records = load_closed_backfill_records_with_snapshot_supplements(backfill_dir)
            due_execute = {
                "schema_version": SCHEMA_VERSION,
                "paper_only": True,
                "diagnostic_only": True,
                "counts_for_live_gate": False,
                "execution": {
                    "status": "executed",
                    "filtered_due_token_count": len(filtered_due_records),
                    "filtered_market_query_count": len(market_queries),
                    "market_queries": market_queries[:20],
                    "targeted_backfill_result": targeted_backfill_result,
                },
                "plan_report": plan_report,
                "after_plan_report": build_orderbook_closed_token_coverage_report(
                    closed_records=after_records,
                    orderbook_snapshots=_load_orderbooks(orderbook_archive_dir),
                    generated_at=generated_at,
                    max_samples=max_samples,
                ),
            }

    effective_due_report = due_execute if isinstance(due_execute, dict) else due_dry_run
    after_plan = effective_due_report.get("after_plan_report")
    if not isinstance(after_plan, dict):
        after_plan = plan_report

    queue_dir = default_strict_gate_queue_dir(paper_journal_dir)
    orderbook_rows = _load_orderbooks(orderbook_archive_dir)
    filtered_orderbook_rows = _filter_records(
        orderbook_rows,
        include_settlement_sources=include_settlement_sources,
        exclude_settlement_sources=exclude_settlement_sources,
        include_station_codes=include_station_codes,
        exclude_station_codes=exclude_station_codes,
    )
    scoped_token_ids = _token_ids(filtered_orderbook_rows)
    backfill_rows = load_closed_backfill_records_with_snapshot_supplements(backfill_dir)
    filtered_backfill_rows = _filter_records(
        backfill_rows,
        include_settlement_sources=include_settlement_sources,
        exclude_settlement_sources=exclude_settlement_sources,
        include_station_codes=include_station_codes,
        exclude_station_codes=exclude_station_codes,
    )
    queue_rows = _filter_queue_by_tokens(load_jsonl(queue_dir / "strict_gate_queue.jsonl"), scoped_token_ids)
    audit_rows = _filter_queue_by_tokens(load_jsonl(Path(paper_journal_dir) / "resolved_audits.jsonl"), scoped_token_ids)
    strict_report = build_strict_gate_replay_report(
        queue_records=queue_rows,
        orderbook_snapshots=filtered_orderbook_rows,
        resolved_outcomes=resolved_outcomes_from_audits_and_backfill(
            audit_records=audit_rows,
            backfill_records=filtered_backfill_rows,
        ),
        replay_time=replay_time,
        size=size,
    )
    overlap_rows = _closed_records_with_archived_overlap(filtered_backfill_rows, filtered_orderbook_rows)
    official_value_records = overlap_rows if overlap_rows else filtered_due_records
    official_value_report = build_official_value_backfill_report(
        official_value_records,
        fetch_external=bool(fetch_external_official_values),
        max_gap_samples=20,
        max_backfill_plan_requests=100,
    )
    ready_supplements = [
        row
        for row in official_value_report.get("supplements") or []
        if isinstance(row, dict) and row.get("status") == "ready"
    ]
    backfill_rows_with_official = apply_official_value_supplements(
        filtered_backfill_rows,
        supplements=ready_supplements,
    )
    truth_audit_report = build_settlement_truth_audit_report(backfill_rows_with_official)
    historical_evidence_report = historical_evidence_from_replay_report(
        strict_report,
        source="due_evidence_pipeline_strict_gate_replay",
    )
    calibration_report = build_settlement_calibration_report(
        backfill_rows_with_official,
        historical_evidence_supplements=historical_evidence_report.get("supplements") or [],
    )
    live_bundle_report = build_live_evidence_bundle_report(
        queue_records=queue_rows,
        orderbook_snapshots=filtered_orderbook_rows,
        closed_backfill_records=filtered_backfill_rows,
        audit_records=audit_rows,
        replay_time=replay_time,
        size=size,
        generated_at=generated_at,
        official_value_supplements=ready_supplements,
        include_settlement_sources=include_settlement_sources,
        exclude_settlement_sources=exclude_settlement_sources,
        include_station_codes=include_station_codes,
        exclude_station_codes=exclude_station_codes,
    )
    readiness_report = build_live_readiness_report(
        journal_dir=paper_journal_dir,
        backfill_dir=backfill_dir,
        strict_gate_queue_dir=queue_dir,
        strict_gate_replay_report=strict_report,
        settlement_calibration_report=calibration_report,
        orderbook_archive_coverage_report=after_plan,
        live_permission=False,
        live_order_path_available=False,
        generated_at=generated_at,
    )
    replay = strict_report.get("replay") if isinstance(strict_report.get("replay"), dict) else {}
    alpha_conclusion = _alpha_conclusion(strict_report, calibration_report)
    payload_diagnostics = (
        targeted_backfill_result.get("payload_diagnostics")
        if isinstance(targeted_backfill_result, dict)
        and isinstance(targeted_backfill_result.get("payload_diagnostics"), dict)
        else {}
    )
    closed_market_slug_count = int(payload_diagnostics.get("closed_market_slug_count") or 0)
    open_market_slug_count = int(payload_diagnostics.get("open_market_slug_count") or 0)
    official_truth_sample_count = int(official_value_report.get("ready_count") or 0)
    if closed_market_slug_count <= 0 and open_market_slug_count > 0 and official_truth_sample_count > 0:
        alpha_conclusion = "official_truth_ready_market_unresolved"
    resolved_pnl_unavailable_reason = None
    next_polymarket_resolution_check_after = None
    unresolved_tokens: list[Dict[str, Any]] = []
    if alpha_conclusion == "official_truth_ready_market_unresolved":
        resolved_pnl_unavailable_reason = "polymarket_market_not_resolved"
        next_polymarket_resolution_check_after = _plus_hours_iso(generated_at or plan_report.get("generated_at"), 1)
        unresolved_tokens = _unresolved_token_rows(filtered_due_records)
    observation_plan = build_official_observation_request_plan(
        filtered_scope_records or official_value_records,
        expected_reuse_market_count=len(filtered_scope_records or official_value_records),
        max_requests=100,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at or plan_report.get("generated_at"),
        "replay_time": replay_time,
        "paper_journal_dir": str(paper_journal_dir),
        "orderbook_archive_dir": str(orderbook_archive_dir),
        "backfill_dir": str(backfill_dir),
        "due_status": due_status,
        "closed_backfill_executed": (
            (due_execute or {}).get("execution", {}).get("status") == "executed"
            if isinstance((due_execute or {}).get("execution"), dict)
            else False
        ),
        "targeted_closed_backfill": {
            "attempted": targeted_backfill_result is not None,
            "closed_market_slug_count": closed_market_slug_count,
            "open_market_slug_count": open_market_slug_count,
            "payload_status": targeted_backfill_result.get("payload_status")
            if isinstance(targeted_backfill_result, dict)
            else None,
        },
        "filters": {
            "include_settlement_source": _list_arg(include_settlement_sources),
            "exclude_settlement_source": _list_arg(exclude_settlement_sources),
            "include_station_code": _list_arg(include_station_codes, upper=True),
            "exclude_station_code": _list_arg(exclude_station_codes, upper=True),
            "allow_partial_official_truth": bool(allow_partial_official_truth),
        },
        "execution_groups": execution_groups,
        "filtered_execution_groups": filtered_execution_groups,
        "next_execution_time_utc": followup.get("earliest_settlement_due_time")
        or followup.get("next_settlement_due_check_after"),
        "before_token_overlap": _overlap_summary(plan_report),
        "after_token_overlap": _overlap_summary(after_plan),
        "strict_replay": {
            "fill_count": replay.get("fill_count"),
            "resolved_fill_count": strict_report.get("resolved_fill_count"),
            "missing_resolution_count": replay.get("missing_resolution_count"),
            "missed_fill_count": replay.get("missed_fill_count"),
            "resolved_pnl_cents": replay.get("resolved_pnl_cents"),
            "brier_score": replay.get("brier_score"),
            "log_loss": replay.get("log_loss"),
            "by_strategy_bucket": strict_report.get("by_strategy_bucket") or [],
            "by_price_bucket": strict_report.get("by_price_bucket") or [],
        },
        "official_truth": {
            "target_scope": "closed_archive_overlap",
            "archived_yes_token_count": len(_archived_yes_token_ids(filtered_orderbook_rows)),
            "overlap_record_count": len(overlap_rows),
            "ready_count": official_value_report.get("ready_count"),
            "gap_count": official_value_report.get("gap_count"),
            "official_truth_sample_count": official_truth_sample_count,
            "request_plan": official_value_report.get("official_observation_backfill_plan"),
            "archive_observation_request_plan": observation_plan,
        },
        "settlement_truth_audit": {
            "hard_conclusion": truth_audit_report.get("hard_conclusion"),
            "audited_with_official_count": truth_audit_report.get("audited_with_official_count"),
            "mismatch_count": truth_audit_report.get("mismatch_count"),
            "gap_count": truth_audit_report.get("gap_count"),
        },
        "settlement_calibration": {
            "hard_conclusion": calibration_report.get("hard_conclusion"),
            "official_truth_sample_count": calibration_report.get("official_truth_sample_count"),
            "probability_score_sample_count": calibration_report.get("probability_score_sample_count"),
            "resolved_pnl_sample_count": calibration_report.get("resolved_pnl_sample_count"),
            "historical_evidence_no_lookahead": calibration_report.get("historical_evidence_no_lookahead"),
        },
        "alpha_conclusion": alpha_conclusion,
        "resolved_pnl_unavailable_reason": resolved_pnl_unavailable_reason,
        "next_polymarket_resolution_check_after": next_polymarket_resolution_check_after,
        "unresolved_tokens": unresolved_tokens,
        "readiness_report_compact": _readiness_compact(readiness_report),
        "component_reports": {
            "due_dry_run": due_dry_run,
            "due_execute": due_execute,
            "strict_gate_replay": strict_report,
            "official_value_backfill": official_value_report,
            "settlement_truth_audit": truth_audit_report,
            "historical_evidence": historical_evidence_report,
            "settlement_calibration": calibration_report,
            "live_evidence_bundle": live_bundle_report,
            "readiness": readiness_report,
        },
        "hard_conclusion": "paper_only_" + alpha_conclusion,
    }


def _write_component_artifacts(summary_output: Path, report: Dict[str, Any]) -> Dict[str, str]:
    out_dir = summary_output.parent
    components = report.get("component_reports") if isinstance(report.get("component_reports"), dict) else {}
    stem = summary_output.stem
    suffix = stem[len("due_pipeline_") :] if stem.startswith("due_pipeline_") else stem
    paths = {
        "strict_gate_replay": out_dir / f"strict_gate_replay_{suffix}.json",
        "official_value_overlap": out_dir / f"official_value_{suffix}.jsonl",
        "settlement_truth_audit": out_dir / f"settlement_truth_audit_{suffix}.json",
        "settlement_calibration": out_dir / f"settlement_calibration_{suffix}.json",
        "live_evidence_bundle": out_dir / f"live_evidence_bundle_{suffix}.json",
        "readiness": out_dir / f"readiness_{suffix}.json",
    }
    if isinstance(components.get("strict_gate_replay"), dict):
        _write_json(paths["strict_gate_replay"], components["strict_gate_replay"])
    official = components.get("official_value_backfill") if isinstance(components.get("official_value_backfill"), dict) else {}
    _write_jsonl(paths["official_value_overlap"], official.get("supplements") or [])
    if isinstance(components.get("settlement_truth_audit"), dict):
        _write_json(paths["settlement_truth_audit"], components["settlement_truth_audit"])
    if isinstance(components.get("settlement_calibration"), dict):
        _write_json(paths["settlement_calibration"], components["settlement_calibration"])
    if isinstance(components.get("live_evidence_bundle"), dict):
        _write_json(paths["live_evidence_bundle"], components["live_evidence_bundle"])
    if isinstance(components.get("readiness"), dict):
        _write_json(paths["readiness"], components["readiness"])
    return {key: str(path) for key, path in paths.items() if path.exists()}


def _summary_for_output(report: Dict[str, Any]) -> Dict[str, Any]:
    summary = dict(report)
    summary.pop("component_reports", None)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paper-only due evidence pipeline from archived orderbooks through calibration/readiness inputs.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ORDERBOOK_ARCHIVE_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--replay-time", default=None)
    parser.add_argument("--size", type=float, default=1.0)
    parser.add_argument("--max-samples", type=int, default=1000)
    parser.add_argument("--execute-closed-backfill", action="store_true")
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--include-settlement-source", action="append", dest="include_settlement_sources")
    parser.add_argument("--exclude-settlement-source", action="append", dest="exclude_settlement_sources")
    parser.add_argument("--include-station-code", action="append", dest="include_station_codes")
    parser.add_argument("--exclude-station-code", action="append", dest="exclude_station_codes")
    parser.add_argument("--allow-partial-official-truth", action="store_true")
    parser.add_argument(
        "--fetch-external-official-values",
        action="store_true",
        help="Fetch supported external official sources during due evidence execution.",
    )
    parser.add_argument("--summary-output", default=None)
    parser.add_argument(
        "--include-component-reports",
        action="store_true",
        help="Print full nested component reports to stdout. Summary output files always stay compact.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_due_evidence_pipeline_report(
        paper_journal_dir=args.paper_journal_dir,
        orderbook_archive_dir=args.orderbook_archive_dir,
        backfill_dir=args.backfill_dir,
        generated_at=args.generated_at,
        replay_time=args.replay_time,
        execute_closed_backfill=bool(args.execute_closed_backfill),
        confirm=args.confirm,
        size=float(args.size),
        max_samples=max(0, int(args.max_samples)),
        include_settlement_sources=args.include_settlement_sources,
        exclude_settlement_sources=args.exclude_settlement_sources,
        include_station_codes=args.include_station_codes,
        exclude_station_codes=args.exclude_station_codes,
        allow_partial_official_truth=bool(args.allow_partial_official_truth),
        fetch_external_official_values=bool(args.fetch_external_official_values),
    )
    output_report = report if args.include_component_reports else _summary_for_output(report)
    if args.summary_output:
        summary_path = Path(args.summary_output)
        artifact_paths = (
            _write_component_artifacts(summary_path, report)
            if report.get("closed_backfill_executed") is True
            else {}
        )
        output_report = dict(output_report)
        output_report["artifact_paths"] = artifact_paths
        _write_json(summary_path, output_report)
    print(json.dumps(output_report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
