from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_closed_historical_replay import (
    build_closed_historical_replay_report,
    build_preresolution_orderbook_replay_report,
    load_closed_snapshot_rows,
)
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements
from src.trading.weather_historical_evidence import historical_evidence_from_replay_report
from src.trading.weather_orderbook_archive_coverage import build_orderbook_closed_token_coverage_report
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR, load_jsonl, utc_now_iso
from src.trading.weather_settlement_calibration import build_settlement_calibration_report
from src.trading.weather_strict_gate_queue import (
    DEFAULT_STRICT_GATE_QUEUE_DIR,
    default_strict_gate_queue_dir,
)
from src.trading.weather_strict_gate_replay import (
    build_strict_gate_replay_report,
    resolved_outcomes_from_audits_and_backfill,
)
from src.weather.official_value_backfill import (
    apply_official_value_supplements,
    build_official_value_backfill_report,
)


LIVE_EVIDENCE_BUNDLE_SCHEMA_VERSION = "polyweather_weather_live_evidence_bundle.v1"


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: str) -> list[Dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _unresolved_replay_summary(strict_gate_replay_report: Dict[str, Any]) -> Dict[str, Any]:
    replay = (
        strict_gate_replay_report.get("replay")
        if isinstance(strict_gate_replay_report.get("replay"), dict)
        else {}
    )
    fills = [row for row in replay.get("fills") or [] if isinstance(row, dict)]
    unresolved = [row for row in fills if row.get("payout") is None]
    return {
        "count": len(unresolved),
        "by_strategy": _count_by(unresolved, "strategy_id", key_name="strategy_id"),
        "by_queue": _count_by(unresolved, "queue_name", key_name="queue_name"),
        "by_city": _count_by(unresolved, "city", key_name="city"),
        "by_bucket_type": _count_by(unresolved, "bucket_type", key_name="bucket_type"),
        "samples": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "strategy_id": row.get("strategy_id"),
                "queue_name": row.get("queue_name"),
                "city": row.get("city"),
                "bucket_type": row.get("bucket_type"),
                "available_at": row.get("available_at"),
                "orderbook_recorded_at": row.get("orderbook_recorded_at"),
                "entry_price": row.get("entry_price"),
                "q_effective": row.get("q_effective"),
                "ev_safe": row.get("ev_safe"),
            }
            for row in unresolved[:10]
        ],
    }


def _gap_summary(
    *,
    strict_gate_replay_report: Dict[str, Any],
    strict_gate_historical_evidence_report: Dict[str, Any],
    closed_historical_replay_report: Dict[str, Any],
    preresolution_orderbook_replay_report: Dict[str, Any],
    orderbook_closed_token_coverage_report: Dict[str, Any],
    historical_evidence_report: Dict[str, Any],
    settlement_calibration_report: Dict[str, Any],
    official_value_backfill_report: Dict[str, Any],
    min_official_truth_samples: int,
    min_probability_score_samples: int,
    min_resolved_pnl_samples: int,
) -> Dict[str, Any]:
    replay = (
        strict_gate_replay_report.get("replay")
        if isinstance(strict_gate_replay_report.get("replay"), dict)
        else {}
    )
    gaps: list[Dict[str, Any]] = []

    def add_gap(
        gap_id: str,
        *,
        observed: Any,
        required: Any,
        next_action: str,
        severity: str = "blocker",
    ) -> None:
        gaps.append(
            {
                "gap_id": gap_id,
                "severity": severity,
                "observed": observed,
                "required": required,
                "next_action": next_action,
            }
        )

    queue_count = int(strict_gate_replay_report.get("queue_record_count") or 0)
    replay_candidate_count = int(strict_gate_replay_report.get("replay_candidate_count") or 0)
    orderbook_count = int(strict_gate_replay_report.get("orderbook_snapshot_count") or 0)
    fill_count = int(replay.get("fill_count") or 0)
    no_visible_orderbook_count = int(replay.get("no_visible_orderbook_count") or 0)
    missed_fill_count = int(replay.get("missed_fill_count") or 0)
    missing_resolution_count = int(replay.get("missing_resolution_count") or 0)
    historical_supplement_count = int(historical_evidence_report.get("supplement_count") or 0)
    strict_historical_supplement_count = int(strict_gate_historical_evidence_report.get("supplement_count") or 0)
    closed_historical_evidence = (
        closed_historical_replay_report.get("historical_evidence")
        if isinstance(closed_historical_replay_report.get("historical_evidence"), dict)
        else {}
    )
    closed_historical_supplement_count = int(closed_historical_evidence.get("supplement_count") or 0)
    closed_historical_input = (
        closed_historical_replay_report.get("input_summary")
        if isinstance(closed_historical_replay_report.get("input_summary"), dict)
        else {}
    )
    preresolution_orderbook_evidence = (
        preresolution_orderbook_replay_report.get("historical_evidence")
        if isinstance(preresolution_orderbook_replay_report.get("historical_evidence"), dict)
        else {}
    )
    preresolution_orderbook_supplement_count = int(preresolution_orderbook_evidence.get("supplement_count") or 0)
    preresolution_orderbook_input = (
        preresolution_orderbook_replay_report.get("input_summary")
        if isinstance(preresolution_orderbook_replay_report.get("input_summary"), dict)
        else {}
    )
    official_truth_count = int(settlement_calibration_report.get("official_truth_sample_count") or 0)
    probability_score_count = int(settlement_calibration_report.get("probability_score_sample_count") or 0)
    resolved_pnl_count = int(settlement_calibration_report.get("resolved_pnl_sample_count") or 0)
    mismatch_count = int(settlement_calibration_report.get("mismatch_count") or 0)
    official_value_ready_count = int(official_value_backfill_report.get("ready_count") or 0)
    official_value_gap_count = int(official_value_backfill_report.get("gap_count") or 0)
    official_observation_plan = (
        official_value_backfill_report.get("official_observation_backfill_plan")
        if isinstance(official_value_backfill_report.get("official_observation_backfill_plan"), dict)
        else {}
    )
    archived_pending_closed_count = int(
        orderbook_closed_token_coverage_report.get("unmatched_archived_token_count") or 0
    )
    archived_pending_due_count = int(
        orderbook_closed_token_coverage_report.get("pending_closed_backfill_due_token_count") or 0
    )
    archived_pending_await_count = int(
        orderbook_closed_token_coverage_report.get("pending_closed_backfill_await_market_end_token_count") or 0
    )
    archived_pending_missing_end_time_count = int(
        orderbook_closed_token_coverage_report.get("pending_closed_backfill_missing_end_time_token_count") or 0
    )
    closed_missing_archive_count = int(
        orderbook_closed_token_coverage_report.get("unmatched_closed_token_count") or 0
    )
    closed_archived_overlap_count = int(
        orderbook_closed_token_coverage_report.get("matched_closed_archived_token_count") or 0
    )

    if queue_count <= 0:
        add_gap(
            "strict_gate_queue_missing",
            observed=queue_count,
            required=">0",
            next_action="Run the paper cycle with strict-gate queue journaling enabled before building the bundle.",
        )
    if replay_candidate_count <= 0:
        add_gap(
            "tokenized_replay_candidates_missing",
            observed=replay_candidate_count,
            required=">0",
            next_action="Ensure strict-gate queue records include Yes token ids and are convertible to replay candidates.",
        )
    if orderbook_count <= 0:
        add_gap(
            "orderbook_archive_missing",
            observed=orderbook_count,
            required=">0",
            next_action="Run active paper collection with orderbook archiving before markets resolve.",
        )
    if no_visible_orderbook_count > 0:
        add_gap(
            "visible_orderbook_missing_for_replay_candidates",
            observed=no_visible_orderbook_count,
            required=0,
            next_action="Archive orderbooks for the queued token ids at timestamps visible to replay_time.",
        )
    if missed_fill_count > 0:
        add_gap(
            "replay_depth_fill_missed",
            observed=missed_fill_count,
            required=0,
            next_action="Lower replay size or require deeper ask liquidity before queueing the candidate.",
        )
    if missing_resolution_count > 0:
        add_gap(
            "resolved_outcome_missing_for_replay_fills",
            observed=missing_resolution_count,
            required=0,
            next_action="Wait for settlement or backfill token-level resolved outcomes before using replay PnL.",
        )
    if fill_count <= 0:
        add_gap(
            "replay_fill_count_zero",
            observed=fill_count,
            required=">0",
            next_action="Fix queue/orderbook coverage so replay can produce executable taker fills.",
        )
    if historical_supplement_count <= 0:
        add_gap(
            "historical_evidence_supplements_missing",
            observed=historical_supplement_count,
            required=">0",
            next_action="Replay must produce fully filled rows with entry price, probability, token id, and available_at.",
        )
    if probability_score_count < int(min_probability_score_samples) and closed_historical_supplement_count <= 0:
        add_gap(
            "closed_historical_replay_evidence_missing",
            observed={
                "hard_conclusion": closed_historical_replay_report.get("hard_conclusion"),
                "candidate_count": closed_historical_input.get("candidate_count"),
                "snapshot_row_count": closed_historical_input.get("snapshot_row_count"),
                "gap_count": closed_historical_input.get("gap_count"),
                "gaps_by_reason": closed_historical_input.get("gaps_by_reason"),
            },
            required="closed-market no-lookahead historical evidence supplements >0",
            next_action="Collect or load pre-resolution closed-market snapshots/orderbooks; post-resolution snapshots cannot be used for calibration evidence.",
        )
    if probability_score_count < int(min_probability_score_samples) and preresolution_orderbook_supplement_count <= 0:
        add_gap(
            "preresolution_orderbook_replay_evidence_missing",
            observed={
                "hard_conclusion": preresolution_orderbook_replay_report.get("hard_conclusion"),
                "candidate_count": preresolution_orderbook_input.get("candidate_count"),
                "archived_orderbook_count": preresolution_orderbook_input.get("archived_orderbook_count"),
                "gap_count": preresolution_orderbook_input.get("gap_count"),
                "gaps_by_reason": preresolution_orderbook_input.get("gaps_by_reason"),
                "closed_archive_token_overlap": {
                    "hard_conclusion": orderbook_closed_token_coverage_report.get("hard_conclusion"),
                    "closed_yes_token_count": orderbook_closed_token_coverage_report.get("closed_yes_token_count"),
                    "archived_unique_token_count": orderbook_closed_token_coverage_report.get("archived_unique_token_count"),
                    "matched_closed_archived_token_count": closed_archived_overlap_count,
                    "unmatched_closed_token_count": closed_missing_archive_count,
                    "unmatched_archived_token_count": archived_pending_closed_count,
                    "pending_closed_backfill_due_token_count": archived_pending_due_count,
                    "pending_closed_backfill_await_market_end_token_count": archived_pending_await_count,
                    "pending_closed_backfill_missing_end_time_token_count": archived_pending_missing_end_time_count,
                    "gaps_by_reason": orderbook_closed_token_coverage_report.get("gaps_by_reason") or [],
                },
            },
            required="closed-market replay evidence from archived pre-resolution orderbooks >0",
            next_action="Keep archiving active market orderbooks before resolution, then refresh closed backfill after those markets settle.",
        )
    if archived_pending_closed_count > 0:
        add_gap(
            "archived_orderbooks_pending_closed_backfill",
            observed={
                "unmatched_archived_token_count": archived_pending_closed_count,
                "due_count": archived_pending_due_count,
                "await_market_end_count": archived_pending_await_count,
                "missing_end_time_count": archived_pending_missing_end_time_count,
                "followup_plan": orderbook_closed_token_coverage_report.get("closed_backfill_followup_plan") or {},
                "matched_closed_archived_token_count": closed_archived_overlap_count,
                "samples": orderbook_closed_token_coverage_report.get("archived_markets_pending_closed_backfill_samples") or [],
            },
            required="archived token ids also present in resolved closed backfill before resolved-PnL replay",
            next_action=(
                "Refresh closed backfill for due archived markets."
                if archived_pending_due_count
                else (
                    "Preserve end_time in future archives and collect a fresh archive batch."
                    if archived_pending_missing_end_time_count
                    else "Wait for archived active markets to reach market end, then refresh closed backfill."
                )
            ),
            severity="info",
        )
    if official_truth_count < int(min_official_truth_samples):
        add_gap(
            "official_truth_samples_insufficient",
            observed=official_truth_count,
            required=int(min_official_truth_samples),
            next_action="Backfill official final values from settlement sources instead of using market-inferred outcomes.",
        )
    if official_truth_count < int(min_official_truth_samples) and official_value_gap_count > 0:
        add_gap(
            "official_value_backfill_gaps",
            observed={
                "ready_count": official_value_ready_count,
                "gap_count": official_value_gap_count,
                "gaps_by_reason": official_value_backfill_report.get("gaps_by_reason") or [],
                "backfill_plan": {
                    key: official_observation_plan.get(key)
                    for key in (
                        "request_count",
                        "returned_request_count",
                        "truncated",
                        "records_covered_count",
                        "skipped_gap_count",
                        "by_settlement_source",
                        "by_gap_reason",
                    )
                },
            },
            required=f"{int(min_official_truth_samples)} ready official-value samples",
            next_action="Populate the local official observation store or run explicit external official-value backfill for supported settlement sources.",
        )
    if probability_score_count < int(min_probability_score_samples):
        add_gap(
            "probability_score_samples_insufficient",
            observed=probability_score_count,
            required=int(min_probability_score_samples),
            next_action="Feed no-lookahead historical probability evidence from replay fills into settlement calibration.",
        )
    if resolved_pnl_count < int(min_resolved_pnl_samples):
        add_gap(
            "resolved_pnl_samples_insufficient",
            observed=resolved_pnl_count,
            required=int(min_resolved_pnl_samples),
            next_action="Attach executable entry prices to officially resolved markets so calibration can compute PnL.",
        )
    if mismatch_count > 0:
        add_gap(
            "settlement_truth_mismatch",
            observed=mismatch_count,
            required=0,
            next_action="Resolve official value / rounding / bucket parsing mismatches before using the market family.",
        )

    return {
        "schema_version": "polyweather_weather_live_evidence_gap_summary.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "primary_blocker": gaps[0]["gap_id"] if gaps else None,
        "gap_count": len(gaps),
        "gaps": gaps,
        "coverage": {
            "queue_record_count": queue_count,
            "replay_candidate_count": replay_candidate_count,
            "orderbook_snapshot_count": orderbook_count,
            "replay_fill_count": fill_count,
            "historical_evidence_supplement_count": historical_supplement_count,
            "strict_gate_historical_evidence_supplement_count": strict_historical_supplement_count,
            "closed_historical_evidence_supplement_count": closed_historical_supplement_count,
            "preresolution_orderbook_evidence_supplement_count": preresolution_orderbook_supplement_count,
            "closed_archive_token_overlap_count": closed_archived_overlap_count,
            "closed_missing_archive_token_count": closed_missing_archive_count,
            "archived_pending_closed_backfill_token_count": archived_pending_closed_count,
            "archived_pending_closed_backfill_due_token_count": archived_pending_due_count,
            "archived_pending_closed_backfill_await_market_end_token_count": archived_pending_await_count,
            "archived_pending_closed_backfill_missing_end_time_token_count": archived_pending_missing_end_time_count,
            "official_value_ready_count": official_value_ready_count,
            "official_value_gap_count": official_value_gap_count,
            "official_truth_sample_count": official_truth_count,
            "probability_score_sample_count": probability_score_count,
            "resolved_pnl_sample_count": resolved_pnl_count,
        },
        "unresolved_replay": _unresolved_replay_summary(strict_gate_replay_report),
    }


def _bundle_blockers(
    *,
    strict_gate_replay_report: Dict[str, Any],
    historical_evidence_report: Dict[str, Any],
    settlement_calibration_report: Dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    replay_conclusion = str(strict_gate_replay_report.get("hard_conclusion") or "")
    if replay_conclusion != "strict_gate_replay_ready_for_ev_audit":
        blockers.append(replay_conclusion or "strict_gate_replay_missing")
    if int(historical_evidence_report.get("supplement_count") or 0) <= 0:
        blockers.append("historical_evidence_supplement_count_zero")
    calibration_conclusion = str(settlement_calibration_report.get("hard_conclusion") or "")
    if calibration_conclusion != "settlement_calibration_ready_diagnostic_only":
        blockers.append(calibration_conclusion or "settlement_calibration_missing")
    for blocker in settlement_calibration_report.get("blockers") or []:
        text = str(blocker or "").strip()
        if text and text not in blockers:
            blockers.append(text)
    return blockers


def _merge_historical_evidence_reports(
    reports: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    materialized = [report for report in reports if isinstance(report, dict)]
    supplements = [
        row
        for report in materialized
        for row in (report.get("supplements") or [])
        if isinstance(row, dict)
    ]
    gap_samples = [
        row
        for report in materialized
        for row in (report.get("gap_samples") or [])
        if isinstance(row, dict)
    ]
    gap_counts: Dict[str, int] = {}
    for report in materialized:
        for row in report.get("gaps_by_reason") or []:
            if not isinstance(row, dict):
                continue
            reason = str(row.get("reason") or "unknown")
            gap_counts[reason] = gap_counts.get(reason, 0) + int(row.get("count") or 0)
    return {
        "schema_version": "polyweather_weather_historical_evidence_bundle.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "source": "live_evidence_bundle_merged",
        "report_count": len(materialized),
        "input_fill_count": sum(int(report.get("input_fill_count") or 0) for report in materialized),
        "supplement_count": len(supplements),
        "gap_count": sum(int(report.get("gap_count") or 0) for report in materialized),
        "gaps_by_reason": [
            {"reason": reason, "count": count}
            for reason, count in sorted(gap_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "supplements": supplements,
        "gap_samples": gap_samples[:20],
        "component_summaries": [
            {
                "source": report.get("source"),
                "supplement_count": report.get("supplement_count"),
                "gap_count": report.get("gap_count"),
                "replay_hard_conclusion": report.get("replay_hard_conclusion"),
            }
            for report in materialized
        ],
    }


def build_live_evidence_bundle_report(
    *,
    queue_records: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    closed_backfill_records: Iterable[Dict[str, Any]],
    closed_snapshot_rows: Optional[Iterable[Dict[str, Any]]] = None,
    audit_records: Optional[Iterable[Dict[str, Any]]] = None,
    replay_time: Optional[str] = None,
    size: float = 1.0,
    queue_names: Optional[Iterable[str]] = None,
    generated_at: Optional[str] = None,
    official_value_supplements: Optional[Iterable[Dict[str, Any]]] = None,
    official_value_repository: Optional[Any] = None,
    fetch_external_official_values: bool = False,
    allow_wunderground_proxy: bool = False,
    max_official_value_gap_samples: int = 10,
    max_official_value_backfill_plan_requests: int = 20,
    min_official_truth_samples: int = 30,
    min_probability_score_samples: int = 30,
    min_resolved_pnl_samples: int = 10,
    min_official_truth_coverage: float = 0.80,
    max_sample_rows: int = 20,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    backfill_rows = [row for row in closed_backfill_records if isinstance(row, dict)]
    explicit_official_supplements = [
        row for row in (official_value_supplements or []) if isinstance(row, dict)
    ]
    if explicit_official_supplements:
        backfill_rows = apply_official_value_supplements(
            backfill_rows,
            supplements=explicit_official_supplements,
        )
    official_value_backfill_report = build_official_value_backfill_report(
        backfill_rows,
        repository=official_value_repository,
        fetch_external=fetch_external_official_values,
        allow_wunderground_proxy=allow_wunderground_proxy,
        max_gap_samples=max_official_value_gap_samples,
        max_backfill_plan_requests=max_official_value_backfill_plan_requests,
    )
    generated_official_supplements = [
        row
        for row in official_value_backfill_report.get("supplements") or []
        if isinstance(row, dict) and row.get("status") == "ready"
    ]
    if generated_official_supplements:
        backfill_rows = apply_official_value_supplements(
            backfill_rows,
            supplements=generated_official_supplements,
        )
    strict_gate_replay_report = build_strict_gate_replay_report(
        queue_records=queue_records,
        orderbook_snapshots=orderbook_snapshots,
        resolved_outcomes=resolved_outcomes_from_audits_and_backfill(
            audit_records=audit_records or [],
            backfill_records=backfill_rows,
        ),
        replay_time=replay_time,
        size=size,
        queue_names=queue_names,
    )
    strict_gate_historical_evidence_report = historical_evidence_from_replay_report(
        strict_gate_replay_report,
        source="live_evidence_bundle_strict_gate_replay",
    )
    closed_historical_replay_report = build_closed_historical_replay_report(
        closed_records=backfill_rows,
        snapshot_rows=closed_snapshot_rows or [],
        replay_time=replay_time,
        size=size,
    )
    closed_historical_evidence_report = (
        dict(closed_historical_replay_report.get("historical_evidence"))
        if isinstance(closed_historical_replay_report.get("historical_evidence"), dict)
        else {}
    )
    if closed_historical_evidence_report:
        closed_historical_evidence_report["replay_schema_version"] = closed_historical_replay_report.get("schema_version")
        closed_historical_evidence_report["replay_time"] = closed_historical_replay_report.get("replay_time")
        closed_historical_evidence_report["replay_hard_conclusion"] = closed_historical_replay_report.get("hard_conclusion")
    preresolution_orderbook_replay_report = build_preresolution_orderbook_replay_report(
        closed_records=backfill_rows,
        orderbook_snapshots=orderbook_snapshots,
        replay_time=replay_time,
        size=size,
    )
    orderbook_closed_token_coverage_report = build_orderbook_closed_token_coverage_report(
        closed_records=backfill_rows,
        orderbook_snapshots=orderbook_snapshots,
        generated_at=generated_at,
        max_samples=max_sample_rows,
    )
    preresolution_orderbook_evidence_report = (
        dict(preresolution_orderbook_replay_report.get("historical_evidence"))
        if isinstance(preresolution_orderbook_replay_report.get("historical_evidence"), dict)
        else {}
    )
    if preresolution_orderbook_evidence_report:
        preresolution_orderbook_evidence_report["replay_schema_version"] = preresolution_orderbook_replay_report.get("schema_version")
        preresolution_orderbook_evidence_report["replay_time"] = preresolution_orderbook_replay_report.get("replay_time")
        preresolution_orderbook_evidence_report["replay_hard_conclusion"] = preresolution_orderbook_replay_report.get("hard_conclusion")
    historical_evidence_report = _merge_historical_evidence_reports(
        [
            strict_gate_historical_evidence_report,
            closed_historical_evidence_report,
            preresolution_orderbook_evidence_report,
        ]
    )
    settlement_calibration_report = build_settlement_calibration_report(
        backfill_rows,
        historical_evidence_supplements=historical_evidence_report.get("supplements") or [],
        min_official_truth_samples=min_official_truth_samples,
        min_probability_score_samples=min_probability_score_samples,
        min_resolved_pnl_samples=min_resolved_pnl_samples,
        min_official_truth_coverage=min_official_truth_coverage,
        max_sample_rows=max_sample_rows,
    )
    blockers = _bundle_blockers(
        strict_gate_replay_report=strict_gate_replay_report,
        historical_evidence_report=historical_evidence_report,
        settlement_calibration_report=settlement_calibration_report,
    )
    gap_summary = _gap_summary(
        strict_gate_replay_report=strict_gate_replay_report,
        strict_gate_historical_evidence_report=strict_gate_historical_evidence_report,
        closed_historical_replay_report=closed_historical_replay_report,
        preresolution_orderbook_replay_report=preresolution_orderbook_replay_report,
        orderbook_closed_token_coverage_report=orderbook_closed_token_coverage_report,
        historical_evidence_report=historical_evidence_report,
        settlement_calibration_report=settlement_calibration_report,
        official_value_backfill_report=official_value_backfill_report,
        min_official_truth_samples=min_official_truth_samples,
        min_probability_score_samples=min_probability_score_samples,
        min_resolved_pnl_samples=min_resolved_pnl_samples,
    )
    return {
        "schema_version": LIVE_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "hard_conclusion": (
            "live_evidence_bundle_ready_for_readiness"
            if not blockers
            else "live_evidence_bundle_needs_more_evidence"
        ),
        "blockers": blockers,
        "gap_summary": gap_summary,
        "official_value_backfill_report": official_value_backfill_report,
        "strict_gate_replay_report": strict_gate_replay_report,
        "strict_gate_historical_evidence_report": strict_gate_historical_evidence_report,
        "closed_historical_replay_report": closed_historical_replay_report,
        "preresolution_orderbook_replay_report": preresolution_orderbook_replay_report,
        "orderbook_closed_token_coverage_report": orderbook_closed_token_coverage_report,
        "historical_evidence_report": historical_evidence_report,
        "settlement_calibration_report": settlement_calibration_report,
        "readiness_report_inputs": {
            "strict_gate_replay_report": True,
            "settlement_calibration_report": True,
        },
    }


def build_live_evidence_bundle_report_from_dirs(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    strict_gate_queue_dir: str | Path | None = DEFAULT_STRICT_GATE_QUEUE_DIR,
    orderbook_archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    official_value_supplements_path: Optional[str | Path] = None,
    replay_time: Optional[str] = None,
    size: float = 1.0,
    queue_names: Optional[Iterable[str]] = None,
    generated_at: Optional[str] = None,
    official_value_repository: Optional[Any] = None,
    fetch_external_official_values: bool = False,
    allow_wunderground_proxy: bool = False,
    max_official_value_gap_samples: int = 10,
    max_official_value_backfill_plan_requests: int = 20,
    min_official_truth_samples: int = 30,
    min_probability_score_samples: int = 30,
    min_resolved_pnl_samples: int = 10,
    min_official_truth_coverage: float = 0.80,
    max_sample_rows: int = 20,
) -> Dict[str, Any]:
    journal_root = Path(paper_journal_dir)
    queue_root = (
        Path(strict_gate_queue_dir)
        if strict_gate_queue_dir is not None
        else default_strict_gate_queue_dir(journal_root)
    )
    orderbook_root = Path(orderbook_archive_dir)
    backfill_root = Path(backfill_dir)
    backfill_rows = load_closed_backfill_records_with_snapshot_supplements(
        backfill_root,
        official_value_supplements_path=official_value_supplements_path,
    )
    return build_live_evidence_bundle_report(
        queue_records=load_jsonl(queue_root / "strict_gate_queue.jsonl"),
        orderbook_snapshots=load_jsonl(orderbook_root / "orderbook_snapshots.jsonl"),
        closed_backfill_records=backfill_rows,
        closed_snapshot_rows=load_closed_snapshot_rows(backfill_root),
        audit_records=load_jsonl(journal_root / "resolved_audits.jsonl"),
        replay_time=replay_time,
        size=size,
        queue_names=queue_names,
        generated_at=generated_at,
        official_value_repository=official_value_repository,
        fetch_external_official_values=fetch_external_official_values,
        allow_wunderground_proxy=allow_wunderground_proxy,
        max_official_value_gap_samples=max_official_value_gap_samples,
        max_official_value_backfill_plan_requests=max_official_value_backfill_plan_requests,
        min_official_truth_samples=min_official_truth_samples,
        min_probability_score_samples=min_probability_score_samples,
        min_resolved_pnl_samples=min_resolved_pnl_samples,
        min_official_truth_coverage=min_official_truth_coverage,
        max_sample_rows=max_sample_rows,
    )
