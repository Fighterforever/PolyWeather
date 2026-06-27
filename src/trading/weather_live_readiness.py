from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_closed_backfill import (
    DEFAULT_BACKFILL_DIR,
    summarize_closed_backfill_journal,
)
from src.trading.weather_execution_calibration import summarize_execution_calibration
from src.trading.weather_maker_quote_journal import summarize_maker_quote_journal
from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    PAPER_FILL_SCHEMA_VERSION,
    _safe_float,
    load_jsonl,
    summarize_markout_strata,
    summarize_paper_journal,
    utc_now_iso,
)
from src.trading.weather_quarantine_surface import build_quarantine_surface_report
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR
from src.trading.weather_resolved_audit import summarize_resolved_audits
from src.trading.weather_resolved_audit import build_resolved_gap_report
from src.trading.weather_temperature_execution_experiment import (
    DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR,
    build_temperature_taker_paper_validation_report,
)


LIVE_READINESS_SCHEMA_VERSION = "polyweather_weather_live_readiness.v1"

MIN_PAPER_FILLS = 30
MIN_MARKOUTS = 30
MIN_RESOLVED_AUDITS = 10
MIN_MARKOUT_WIN_RATE = 0.55
MIN_RESOLVED_WIN_RATE = 0.55
MIN_MEAN_MARKOUT_CENTS = 0.0
MIN_RESOLVED_TOTAL_PNL_CENTS = 0.0


def _record_key(record: Dict[str, Any], fallback_prefix: str, index: int) -> str:
    value = str(record.get("fill_id") or record.get("id") or "").strip()
    return value or f"{fallback_prefix}:{index}"


def _latest_by_fill_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        latest[_record_key(record, "row", index)] = record
    return latest


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _score_coverage(count: int, target: int, points: float) -> float:
    if target <= 0:
        return 0.0
    return points * min(1.0, max(0.0, count / target))


def _signal_counts(
    *,
    signal_report: Optional[Dict[str, Any]],
    journal_summary: Dict[str, Any],
) -> Tuple[Optional[int], int, str]:
    if isinstance(signal_report, dict):
        summary = signal_report.get("summary") if isinstance(signal_report.get("summary"), dict) else {}
        candidate_count = int(summary.get("candidate_count") or 0)
        watch_count = int(summary.get("watch_count") or 0)
        return candidate_count, candidate_count + watch_count, "current_signal_report"
    return None, int(journal_summary.get("candidate_fill_count") or 0) + int(journal_summary.get("watch_fill_count") or 0), "paper_journal"


def _build_blockers(
    *,
    current_candidate_count: Optional[int],
    paper_fill_count: int,
    marked_count: int,
    mean_markout_cents: Optional[float],
    markout_win_rate: Optional[float],
    resolved_count: int,
    resolved_win_rate: Optional[float],
    resolved_total_pnl_cents: Optional[float],
    live_permission: bool,
    resolved_gap_hard_conclusion: Optional[str] = None,
) -> List[str]:
    blockers: List[str] = []
    if current_candidate_count is None:
        blockers.append("current_signal_report_missing")
    elif current_candidate_count <= 0:
        blockers.append("no_current_weather_signal")
    if paper_fill_count < MIN_PAPER_FILLS:
        blockers.append(f"insufficient_paper_fills_{paper_fill_count}_of_{MIN_PAPER_FILLS}")
    if marked_count < MIN_MARKOUTS:
        blockers.append(f"insufficient_markouts_{marked_count}_of_{MIN_MARKOUTS}")
    if mean_markout_cents is None:
        blockers.append("mean_markout_missing")
    elif mean_markout_cents < MIN_MEAN_MARKOUT_CENTS:
        blockers.append("negative_mean_markout_cents")
    if markout_win_rate is None:
        blockers.append("markout_win_rate_missing")
    elif markout_win_rate < MIN_MARKOUT_WIN_RATE:
        blockers.append("markout_win_rate_below_55pct")
    if resolved_count < MIN_RESOLVED_AUDITS:
        blockers.append(f"insufficient_resolved_audits_{resolved_count}_of_{MIN_RESOLVED_AUDITS}")
    if resolved_win_rate is None:
        blockers.append("resolved_win_rate_missing")
    elif resolved_win_rate < MIN_RESOLVED_WIN_RATE:
        blockers.append("resolved_win_rate_below_55pct")
    if resolved_total_pnl_cents is None:
        blockers.append("resolved_total_pnl_missing")
    elif resolved_total_pnl_cents < MIN_RESOLVED_TOTAL_PNL_CENTS:
        blockers.append("resolved_total_pnl_negative")
    if resolved_count < MIN_RESOLVED_AUDITS:
        if resolved_gap_hard_conclusion == "resolved_gap_wait_for_settlement":
            blockers.append("resolved_audit_waiting_for_settlement")
        elif resolved_gap_hard_conclusion == "resolved_gap_contains_overdue_backfill_work":
            blockers.append("resolved_audit_overdue_backfill_work")
    if not live_permission:
        blockers.append("live_permission_false")
    return blockers


def _compact_resolved_gap_report(report: Dict[str, Any]) -> Dict[str, Any]:
    rows = report.get("rows") if isinstance(report.get("rows"), list) else []
    due_rows = [
        row
        for row in rows
        if row.get("gap_status") not in {"not_due", "within_settlement_grace"}
    ]
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "settlement_grace_hours": report.get("settlement_grace_hours"),
        "fill_count": report.get("fill_count"),
        "live_gate_fill_count": report.get("live_gate_fill_count"),
        "backfill_record_count": report.get("backfill_record_count"),
        "overdue_count": report.get("overdue_count"),
        "due_or_gap_count": len(due_rows),
        "gap_status_counts": report.get("gap_status_counts") or [],
        "next_action_counts": report.get("next_action_counts") or [],
        "live_gate_gap_status_counts": report.get("live_gate_gap_status_counts") or [],
    }


def _compact_quarantine_surface_group(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "group_name": row.get("group_name"),
        "dimensions": row.get("dimensions") or {},
        "action": row.get("action"),
        "taker_marked_count": row.get("taker_marked_count"),
        "mean_taker_markout_cents": row.get("mean_taker_markout_cents"),
        "taker_win_rate": row.get("taker_win_rate"),
        "maker_quote_count": row.get("maker_quote_count"),
        "mean_maker_markout_cents": row.get("mean_maker_markout_cents"),
        "maker_win_rate": row.get("maker_win_rate"),
        "failure_reasons": row.get("failure_reasons") or [],
    }


def _compact_quarantine_surface_report(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "thresholds": report.get("thresholds") or {},
        "evidence": report.get("evidence") or {},
        "action_counts": report.get("action_counts") or [],
        "promote_group_count": report.get("promote_group_count"),
        "maker_only_watch_count": report.get("maker_only_watch_count"),
        "cooldown_group_count": report.get("cooldown_group_count"),
        "continue_sampling_group_count": report.get("continue_sampling_group_count"),
        "promote_groups": [
            _compact_quarantine_surface_group(row)
            for row in (report.get("promote_groups") or [])[:5]
            if isinstance(row, dict)
        ],
        "maker_only_watch_groups": [
            _compact_quarantine_surface_group(row)
            for row in (report.get("maker_only_watch_groups") or [])[:5]
            if isinstance(row, dict)
        ],
        "cooldown_groups": [
            _compact_quarantine_surface_group(row)
            for row in (report.get("cooldown_groups") or [])[:5]
            if isinstance(row, dict)
        ],
        "continue_sampling_groups": [
            _compact_quarantine_surface_group(row)
            for row in (report.get("continue_sampling_groups") or [])[:5]
            if isinstance(row, dict)
        ],
    }


def _compact_maker_quote_blocker_calibration(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "thresholds": report.get("thresholds") or {},
        "strata_summary": report.get("strata_summary") or {},
        "blocker_count": report.get("blocker_count"),
        "specific_candidate_blocker_count": report.get("specific_candidate_blocker_count"),
        "eligible_blocker_count": report.get("eligible_blocker_count"),
        "keep_blocked_count": report.get("keep_blocked_count"),
        "collect_more_count": report.get("collect_more_count"),
        "action_counts": report.get("action_counts") or [],
        "specificity_counts": report.get("specificity_counts") or [],
        "blockers": [
            {
                "reason": row.get("reason"),
                "group": row.get("group"),
                "specificity": row.get("specificity"),
                "dimensions": row.get("dimensions") or {},
                "current_hit_count": row.get("current_hit_count"),
                "current_candidate_like_count": row.get("current_candidate_like_count"),
                "action": row.get("action"),
                "failure_reasons": row.get("failure_reasons") or [],
                "evidence": row.get("evidence") or {},
            }
            for row in (report.get("blockers") or [])[:10]
            if isinstance(row, dict)
        ],
    }


def _compact_model_coverage_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "thresholds": report.get("thresholds") or {},
        "total_rows": report.get("total_rows"),
        "surface_ready_count": report.get("surface_ready_count"),
        "model_gap_surface_ready_count": report.get("model_gap_surface_ready_count"),
        "short_horizon_model_gap_surface_ready_count": report.get("short_horizon_model_gap_surface_ready_count"),
        "long_horizon_model_gap_surface_ready_count": report.get("long_horizon_model_gap_surface_ready_count"),
        "family_counts": report.get("family_counts") or [],
        "model_join_status_counts": report.get("model_join_status_counts") or [],
        "model_build_queue_count": report.get("model_build_queue_count"),
        "long_horizon_watch_queue_count": report.get("long_horizon_watch_queue_count"),
        "model_build_queue": [
            {
                "market_family": row.get("market_family"),
                "model_gap_surface_ready_count": row.get("model_gap_surface_ready_count"),
                "row_count": row.get("row_count"),
                "mean_model_gap_liquidity": row.get("mean_model_gap_liquidity"),
                "median_model_gap_spread": row.get("median_model_gap_spread"),
                "recommended_action": row.get("recommended_action"),
            }
            for row in (report.get("model_build_queue") or [])[:5]
            if isinstance(row, dict)
        ],
        "long_horizon_watch_queue": [
            {
                "market_family": row.get("market_family"),
                "long_horizon_model_gap_surface_ready_count": row.get(
                    "long_horizon_model_gap_surface_ready_count"
                ),
                "row_count": row.get("row_count"),
                "mean_model_gap_liquidity": row.get("mean_model_gap_liquidity"),
                "median_model_gap_spread": row.get("median_model_gap_spread"),
                "recommended_action": row.get("recommended_action"),
            }
            for row in (report.get("long_horizon_watch_queue") or [])[:5]
            if isinstance(row, dict)
        ],
    }


def _compact_temperature_opportunity_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "config": report.get("config") or {},
        "scanned_temperature_count": report.get("scanned_temperature_count"),
        "excluded_bucket_count": report.get("excluded_bucket_count"),
        "outside_horizon_count": report.get("outside_horizon_count"),
        "category_counts": report.get("category_counts") or [],
        "eligible_for_formal_paper_count": report.get("eligible_for_formal_paper_count"),
        "blocked_by_negative_maker_count": report.get("blocked_by_negative_maker_count"),
        "collect_more_maker_evidence_count": report.get("collect_more_maker_evidence_count"),
        "blocked_by_market_surface_count": report.get("blocked_by_market_surface_count"),
        "blocked_by_non_maker_risk_count": report.get("blocked_by_non_maker_risk_count"),
        "eligible_for_formal_paper": [
            {
                "market_slug": row.get("market_slug"),
                "city": row.get("city"),
                "side": row.get("side"),
                "bucket_type": row.get("bucket_type"),
                "price": row.get("price"),
                "spread": row.get("spread"),
                "edge_percent": row.get("edge_percent"),
                "maker_hit_state": row.get("maker_hit_state"),
            }
            for row in (report.get("eligible_for_formal_paper") or [])[:5]
            if isinstance(row, dict)
        ],
        "blocked_by_negative_maker_evidence": [
            {
                "market_slug": row.get("market_slug"),
                "city": row.get("city"),
                "side": row.get("side"),
                "bucket_type": row.get("bucket_type"),
                "price": row.get("price"),
                "spread": row.get("spread"),
                "edge_percent": row.get("edge_percent"),
                "maker_hit_state": row.get("maker_hit_state"),
                "maker_hit_details": row.get("maker_hit_details") or [],
            }
            for row in (report.get("blocked_by_negative_maker_evidence") or [])[:5]
            if isinstance(row, dict)
        ],
        "collect_more_maker_evidence": [
            {
                "market_slug": row.get("market_slug"),
                "city": row.get("city"),
                "side": row.get("side"),
                "bucket_type": row.get("bucket_type"),
                "price": row.get("price"),
                "spread": row.get("spread"),
                "edge_percent": row.get("edge_percent"),
                "maker_hit_state": row.get("maker_hit_state"),
                "maker_hit_details": row.get("maker_hit_details") or [],
            }
            for row in (report.get("collect_more_maker_evidence") or [])[:5]
            if isinstance(row, dict)
        ],
    }


def _compact_temperature_execution_experiment_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "live_gate": report.get("live_gate"),
        "config": report.get("config") or {},
        "blocked_negative_maker_source_count": report.get("blocked_negative_maker_source_count"),
        "experiment_count": report.get("experiment_count"),
        "action_counts": report.get("action_counts") or [],
        "collect_formal_taker_paper_count": report.get("collect_formal_taker_paper_count"),
        "collect_offset_ladder_shadow_quote_count": report.get(
            "collect_offset_ladder_shadow_quote_count"
        ),
        "skip_current_surface_count": report.get("skip_current_surface_count"),
        "experiments": [
            {
                "market_slug": row.get("market_slug"),
                "city": row.get("city"),
                "side": row.get("side"),
                "bucket_type": row.get("bucket_type"),
                "price": row.get("price"),
                "spread": row.get("spread"),
                "edge_percent": row.get("edge_percent"),
                "maker_evidence": row.get("maker_evidence") or {},
                "taker_cross": row.get("taker_cross") or {},
                "offsets_covering_adverse_count": row.get("offsets_covering_adverse_count"),
                "next_action": row.get("next_action"),
            }
            for row in (report.get("experiments") or [])[:5]
            if isinstance(row, dict)
        ],
    }


def _compact_temperature_taker_validation_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "next_action": report.get("next_action"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "live_gate": report.get("live_gate"),
        "config": report.get("config") or {},
        "summary": report.get("summary") or {},
        "horizon_status": report.get("horizon_status") or [],
        "missing_horizons": report.get("missing_horizons") or [],
        "weak_horizons": report.get("weak_horizons") or [],
        "blockers": report.get("blockers") or [],
    }


def _compact_current_signal_taker_validation_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "schema_version": report.get("schema_version"),
        "hard_conclusion": report.get("hard_conclusion"),
        "next_action": report.get("next_action"),
        "paper_only": report.get("paper_only"),
        "counts_for_live_gate": report.get("counts_for_live_gate"),
        "live_gate": report.get("live_gate"),
        "summary": report.get("summary") or {},
        "horizon_status": report.get("horizon_status") or [],
        "missing_horizons": report.get("missing_horizons") or [],
        "weak_horizons": report.get("weak_horizons") or [],
        "latest_strata_summary": report.get("latest_strata_summary") or {},
        "blockers": report.get("blockers") or [],
    }


def _compact_signal_risk_filter(signal_report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(signal_report, dict):
        return None
    risk_filter = signal_report.get("risk_filter")
    if not isinstance(risk_filter, dict):
        return None
    return {
        "enabled": risk_filter.get("enabled"),
        "mode": risk_filter.get("mode"),
        "source_rule_count": risk_filter.get("source_rule_count"),
        "initial_rule_count": risk_filter.get("initial_rule_count"),
        "rule_count": risk_filter.get("rule_count"),
        "hit_count": risk_filter.get("hit_count"),
        "risk_rule_reject_count": risk_filter.get("risk_rule_reject_count"),
        "risk_only_reject_count": risk_filter.get("risk_only_reject_count"),
        "pre_suppression_saturated_rule_count": risk_filter.get("pre_suppression_saturated_rule_count"),
        "pre_suppression_saturated_group_count": risk_filter.get("pre_suppression_saturated_group_count"),
        "suppressed_saturated_rule_count": risk_filter.get("suppressed_saturated_rule_count"),
        "suppressed_saturated_group_rule_count": risk_filter.get("suppressed_saturated_group_rule_count"),
        "saturated_rule_count": risk_filter.get("saturated_rule_count"),
        "saturated_group_count": risk_filter.get("saturated_group_count"),
        "suppressed_saturated_rules": [
            {
                "group": row.get("group"),
                "dimensions": row.get("dimensions") or {},
                "source": row.get("source"),
                "reason": row.get("reason"),
                "saturation_reason": row.get("saturation_reason"),
                "count": row.get("count"),
                "mean_markout_cents": row.get("mean_markout_cents"),
                "win_rate": row.get("win_rate"),
                "mean_maker_markout_cents": row.get("mean_maker_markout_cents"),
            }
            for row in (risk_filter.get("suppressed_saturated_rules") or [])[:5]
            if isinstance(row, dict)
        ],
        "suppressed_saturated_group_rules": [
            {
                "group": row.get("group"),
                "dimensions": row.get("dimensions") or {},
                "source": row.get("source"),
                "reason": row.get("reason"),
                "saturation_group": row.get("saturation_group"),
                "saturation_group_coverage": row.get("saturation_group_coverage"),
                "saturation_group_reason_count": row.get("saturation_group_reason_count"),
                "count": row.get("count"),
                "mean_markout_cents": row.get("mean_markout_cents"),
                "win_rate": row.get("win_rate"),
                "mean_maker_markout_cents": row.get("mean_maker_markout_cents"),
            }
            for row in (risk_filter.get("suppressed_saturated_group_rules") or [])[:10]
            if isinstance(row, dict)
        ],
        "saturated_rules": [
            {
                "reason": row.get("reason"),
                "scope": row.get("scope"),
                "count": row.get("count"),
                "total_rows": row.get("total_rows"),
                "coverage": row.get("coverage"),
            }
            for row in (risk_filter.get("saturated_rules") or [])[:5]
            if isinstance(row, dict)
        ],
        "saturated_groups": [
            {
                "group": row.get("group"),
                "scope": row.get("scope"),
                "count": row.get("count"),
                "total_rows": row.get("total_rows"),
                "coverage": row.get("coverage"),
                "reason_count": row.get("reason_count"),
                "sample_reasons": row.get("sample_reasons") or [],
            }
            for row in (risk_filter.get("saturated_groups") or [])[:10]
            if isinstance(row, dict)
        ],
    }


def _compact_current_signal_diagnostics(signal_report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(signal_report, dict):
        return None
    summary = signal_report.get("summary") if isinstance(signal_report.get("summary"), dict) else {}
    coverage = (
        signal_report.get("coverage_diagnostics")
        if isinstance(signal_report.get("coverage_diagnostics"), dict)
        else {}
    )
    source = (
        signal_report.get("source_diagnostics")
        if isinstance(signal_report.get("source_diagnostics"), dict)
        else {}
    )
    candidate_gap = (
        signal_report.get("candidate_gap_report")
        if isinstance(signal_report.get("candidate_gap_report"), dict)
        else {}
    )
    near_candidates = [
        {
            "question": row.get("question"),
            "market_slug": row.get("market_slug"),
            "city": row.get("city"),
            "market_family": row.get("market_family"),
            "bucket_type": row.get("bucket_type"),
            "bucket_label": row.get("bucket_label"),
            "side": row.get("side"),
            "price": row.get("price"),
            "market_probability": row.get("market_probability"),
            "model_probability": row.get("model_probability"),
            "edge_percent": row.get("edge_percent"),
            "spread": row.get("spread"),
            "liquidity": row.get("liquidity"),
            "non_risk_blockers": row.get("non_risk_blockers") or [],
            "risk_rule_hits": row.get("risk_rule_hits") or [],
            "risk_rule_scope_counts": row.get("risk_rule_scope_counts") or [],
            "would_be_decision_without_risk_rules": row.get(
                "would_be_decision_without_risk_rules"
            ),
        }
        for row in (candidate_gap.get("near_candidates") or [])[:10]
        if isinstance(row, dict)
    ]
    candidate_gap_counts = {
        key: value
        for key, value in candidate_gap.items()
        if key.endswith("_count") and isinstance(value, (int, float))
    }
    return {
        "summary": {
            "total_rows": summary.get("total_rows"),
            "candidate_count": summary.get("candidate_count"),
            "watch_count": summary.get("watch_count"),
            "quarantine_count": summary.get("quarantine_count"),
            "reject_count": summary.get("reject_count"),
            "quarantine_candidate_without_risk_count": summary.get(
                "quarantine_candidate_without_risk_count"
            ),
            "quarantine_risk_only_count": summary.get("quarantine_risk_only_count"),
            "quarantine_near_miss_count": summary.get("quarantine_near_miss_count"),
            "top_candidate_score": summary.get("top_candidate_score"),
        },
        "top_rejection_reasons": [
            {
                "reason": row.get("reason"),
                "count": row.get("count"),
            }
            for row in (signal_report.get("rejection_reason_counts") or [])[:15]
            if isinstance(row, dict)
        ],
        "quarantine_by_reason": coverage.get("quarantine_by_reason") or [],
        "quarantine_by_blocker_scope": coverage.get("quarantine_by_blocker_scope") or [],
        "decision_by_market_family": coverage.get("decision_by_market_family") or [],
        "model_join": source.get("model_join") if isinstance(source.get("model_join"), dict) else None,
        "candidate_gap_counts": candidate_gap_counts,
        "near_candidates": near_candidates,
    }


def _build_score_components(
    *,
    current_candidate_count: Optional[int],
    historical_candidate_fill_count: int,
    paper_fill_count: int,
    marked_count: int,
    mean_markout_cents: Optional[float],
    markout_win_rate: Optional[float],
    resolved_count: int,
    resolved_win_rate: Optional[float],
    resolved_total_pnl_cents: Optional[float],
    live_permission: bool,
    live_gate: bool,
) -> Dict[str, float]:
    if current_candidate_count is None:
        signal_available = historical_candidate_fill_count > 0
    else:
        signal_available = current_candidate_count > 0
    signal_score = 20.0 if signal_available else 0.0

    evidence_volume_score = _score_coverage(paper_fill_count, MIN_PAPER_FILLS, 10.0)
    evidence_volume_score += _score_coverage(marked_count, MIN_MARKOUTS, 10.0)

    forward_markout_score = _score_coverage(marked_count, MIN_MARKOUTS, 5.0)
    if mean_markout_cents is not None and mean_markout_cents >= MIN_MEAN_MARKOUT_CENTS:
        forward_markout_score += _score_coverage(marked_count, MIN_MARKOUTS, 10.0)
    if markout_win_rate is not None and markout_win_rate >= MIN_MARKOUT_WIN_RATE:
        forward_markout_score += _score_coverage(marked_count, MIN_MARKOUTS, 10.0)

    resolved_audit_score = _score_coverage(resolved_count, MIN_RESOLVED_AUDITS, 5.0)
    if resolved_win_rate is not None and resolved_win_rate >= MIN_RESOLVED_WIN_RATE:
        resolved_audit_score += _score_coverage(resolved_count, MIN_RESOLVED_AUDITS, 10.0)
    if (
        resolved_total_pnl_cents is not None
        and resolved_total_pnl_cents >= MIN_RESOLVED_TOTAL_PNL_CENTS
    ):
        resolved_audit_score += _score_coverage(resolved_count, MIN_RESOLVED_AUDITS, 10.0)

    safety_score = 5.0
    if live_gate and live_permission:
        safety_score = 10.0

    return {
        "signal_availability": round(signal_score, 6),
        "evidence_volume": round(evidence_volume_score, 6),
        "forward_markout": round(forward_markout_score, 6),
        "resolved_audit": round(resolved_audit_score, 6),
        "runtime_safety": round(safety_score, 6),
    }


def build_live_readiness_report(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    quarantine_journal_dir: str | Path = DEFAULT_QUARANTINE_JOURNAL_DIR,
    signal_report: Optional[Dict[str, Any]] = None,
    quarantine_surface_report: Optional[Dict[str, Any]] = None,
    maker_quote_blocker_calibration_report: Optional[Dict[str, Any]] = None,
    model_coverage_report: Optional[Dict[str, Any]] = None,
    temperature_opportunity_report: Optional[Dict[str, Any]] = None,
    temperature_execution_experiment_report: Optional[Dict[str, Any]] = None,
    current_signal_taker_validation_report: Optional[Dict[str, Any]] = None,
    temperature_taker_validation_report: Optional[Dict[str, Any]] = None,
    temperature_taker_journal_dir: str | Path = DEFAULT_TEMPERATURE_TAKER_JOURNAL_DIR,
    include_temperature_taker_validation: bool = False,
    include_quarantine_surface: bool = False,
    live_permission: bool = False,
    generated_at: Optional[str] = None,
    settlement_grace_hours: float = 24.0,
    quarantine_surface_min_decision_count: int = 5,
    quarantine_surface_min_promote_count: int = 5,
    quarantine_surface_min_mean_markout_cents: float = 0.0,
    quarantine_surface_min_win_rate: float = 0.55,
    quarantine_surface_min_maker_quote_count: int = 0,
    quarantine_surface_min_maker_mean_markout_cents: float = 0.0,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    journal_summary = summarize_paper_journal(journal_root)
    markout_strata_summary = summarize_markout_strata(journal_root, latest_only=True)
    execution_calibration_summary = summarize_execution_calibration(journal_root, latest_only=True)
    maker_quote_summary = summarize_maker_quote_journal(journal_root, latest_only=True)
    resolved_summary = summarize_resolved_audits(journal_root)
    backfill_summary = summarize_closed_backfill_journal(backfill_dir)
    resolved_gap_report = build_resolved_gap_report(
        journal_dir=journal_root,
        backfill_dir=backfill_dir,
        generated_at=generated_at,
        settlement_grace_hours=settlement_grace_hours,
    )
    resolved_gap_summary = _compact_resolved_gap_report(resolved_gap_report)
    quarantine_surface_summary = None
    if quarantine_surface_report is not None:
        quarantine_surface_summary = _compact_quarantine_surface_report(quarantine_surface_report)
    elif include_quarantine_surface:
        quarantine_surface_summary = _compact_quarantine_surface_report(
            build_quarantine_surface_report(
                paper_journal_dir=journal_root,
                quarantine_journal_dir=quarantine_journal_dir,
                min_decision_count=quarantine_surface_min_decision_count,
                min_promote_count=quarantine_surface_min_promote_count,
                min_mean_markout_cents=quarantine_surface_min_mean_markout_cents,
                min_win_rate=quarantine_surface_min_win_rate,
                min_maker_quote_count=quarantine_surface_min_maker_quote_count,
                min_maker_mean_markout_cents=quarantine_surface_min_maker_mean_markout_cents,
                generated_at=generated_at,
            )
        )
    if temperature_taker_validation_report is None and include_temperature_taker_validation:
        temperature_taker_validation_report = build_temperature_taker_paper_validation_report(
            journal_dir=temperature_taker_journal_dir,
            generated_at=generated_at,
        )

    fills_by_id = _latest_by_fill_id(load_jsonl(journal_root / "paper_fills.jsonl"))
    markouts_by_fill = _latest_by_fill_id(load_jsonl(journal_root / "markouts.jsonl"))
    audits_by_fill = _latest_by_fill_id(load_jsonl(journal_root / "resolved_audits.jsonl"))

    paper_fills = [
        record
        for record in fills_by_id.values()
        if record.get("counts_for_live_gate") is not False
        and record.get("signal_bucket") != "quarantine"
    ]
    eligible_fill_ids = {str(record.get("fill_id")) for record in paper_fills}
    marked = [
        record
        for record in markouts_by_fill.values()
        if str(record.get("fill_id")) in eligible_fill_ids
        if record.get("status") == "marked" and _safe_float(record.get("markout_cents")) is not None
    ]
    markout_values = [
        float(record["markout_cents"])
        for record in marked
        if _safe_float(record.get("markout_cents")) is not None
    ]
    markout_wins = [value for value in markout_values if value > 0.0]
    resolved = [
        record
        for record in audits_by_fill.values()
        if str(record.get("fill_id")) in eligible_fill_ids
        and record.get("status") == "resolved"
    ]
    resolved_wins = [record for record in resolved if record.get("winning") is True]
    resolved_pnl_values = [
        _safe_float(record.get("pnl_cents"))
        for record in resolved
        if _safe_float(record.get("pnl_cents")) is not None
    ]
    resolved_total_pnl_cents = (
        round(sum(value for value in resolved_pnl_values if value is not None), 6)
        if resolved_pnl_values
        else None
    )

    current_candidate_count, signal_count_for_reporting, signal_source = _signal_counts(
        signal_report=signal_report,
        journal_summary=journal_summary,
    )
    paper_fill_count = len(paper_fills)
    historical_candidate_fill_count = len(
        [record for record in paper_fills if record.get("signal_bucket") == "candidates"]
    )
    marked_count = len(marked)
    resolved_count = len(resolved)
    mean_markout_cents = _mean(markout_values)
    markout_win_rate = _ratio(len(markout_wins), marked_count)
    resolved_win_rate = _ratio(len(resolved_wins), resolved_count)

    evidence_gate = (
        current_candidate_count is not None
        and current_candidate_count > 0
        and paper_fill_count >= MIN_PAPER_FILLS
        and marked_count >= MIN_MARKOUTS
        and mean_markout_cents is not None
        and mean_markout_cents >= MIN_MEAN_MARKOUT_CENTS
        and markout_win_rate is not None
        and markout_win_rate >= MIN_MARKOUT_WIN_RATE
        and resolved_count >= MIN_RESOLVED_AUDITS
        and resolved_win_rate is not None
        and resolved_win_rate >= MIN_RESOLVED_WIN_RATE
        and resolved_total_pnl_cents is not None
        and resolved_total_pnl_cents >= MIN_RESOLVED_TOTAL_PNL_CENTS
    )
    blockers = _build_blockers(
        current_candidate_count=current_candidate_count,
        paper_fill_count=paper_fill_count,
        marked_count=marked_count,
        mean_markout_cents=mean_markout_cents,
        markout_win_rate=markout_win_rate,
        resolved_count=resolved_count,
        resolved_win_rate=resolved_win_rate,
        resolved_total_pnl_cents=resolved_total_pnl_cents,
        live_permission=live_permission,
        resolved_gap_hard_conclusion=str(resolved_gap_summary.get("hard_conclusion") or ""),
    )
    live_gate = bool(evidence_gate)
    live_authorization_pct = 100 if live_gate and live_permission else 0
    score_components = _build_score_components(
        current_candidate_count=current_candidate_count,
        historical_candidate_fill_count=historical_candidate_fill_count,
        paper_fill_count=paper_fill_count,
        marked_count=marked_count,
        mean_markout_cents=mean_markout_cents,
        markout_win_rate=markout_win_rate,
        resolved_count=resolved_count,
        resolved_win_rate=resolved_win_rate,
        resolved_total_pnl_cents=resolved_total_pnl_cents,
        live_permission=live_permission,
        live_gate=live_gate,
    )
    readiness_pct = round(sum(score_components.values()), 2)
    diagnostic_blockers: List[str] = []
    current_signal_diagnostics = _compact_current_signal_diagnostics(signal_report)
    if isinstance(current_signal_diagnostics, dict):
        signal_diag_summary = current_signal_diagnostics.get("summary") or {}
        candidate_count_for_diag = int(signal_diag_summary.get("candidate_count") or 0)
        quarantine_count_for_diag = int(signal_diag_summary.get("quarantine_count") or 0)
        if candidate_count_for_diag == 0 and quarantine_count_for_diag > 0:
            diagnostic_blockers.append("current_signal_quarantine_only")
        if any(
            row.get("would_be_decision_without_risk_rules") == "candidate"
            for row in current_signal_diagnostics.get("near_candidates") or []
            if isinstance(row, dict)
        ):
            diagnostic_blockers.append("current_signal_risk_rules_block_candidate_like_rows")
    signal_risk_filter_summary = _compact_signal_risk_filter(signal_report)
    if isinstance(signal_risk_filter_summary, dict):
        if int(signal_risk_filter_summary.get("suppressed_saturated_rule_count") or 0) > 0:
            diagnostic_blockers.append("risk_filter_suppressed_saturated_broad_rules")
        if int(signal_risk_filter_summary.get("suppressed_saturated_group_rule_count") or 0) > 0:
            diagnostic_blockers.append("risk_filter_suppressed_saturated_partition_rules")
        if int(signal_risk_filter_summary.get("saturated_rule_count") or 0) > 0:
            diagnostic_blockers.append("risk_filter_saturated_rules_remaining")
        if int(signal_risk_filter_summary.get("saturated_group_count") or 0) > 0:
            diagnostic_blockers.append("risk_filter_saturated_groups_remaining")
    if isinstance(quarantine_surface_summary, dict):
        quarantine_conclusion = str(quarantine_surface_summary.get("hard_conclusion") or "")
        if quarantine_conclusion in {
            "quarantine_surface_currently_negative",
            "quarantine_surface_collect_more",
        }:
            diagnostic_blockers.append(quarantine_conclusion)
    maker_quote_blocker_summary = _compact_maker_quote_blocker_calibration(
        maker_quote_blocker_calibration_report
    )
    if isinstance(maker_quote_blocker_summary, dict):
        maker_quote_conclusion = str(maker_quote_blocker_summary.get("hard_conclusion") or "")
        if maker_quote_conclusion in {
            "maker_quote_specific_blockers_currently_negative",
            "maker_quote_blockers_need_more_evidence",
        }:
            diagnostic_blockers.append(maker_quote_conclusion)
    model_coverage_summary = _compact_model_coverage_report(model_coverage_report)
    if isinstance(model_coverage_summary, dict):
        model_coverage_conclusion = str(model_coverage_summary.get("hard_conclusion") or "")
        if model_coverage_conclusion == "non_temperature_model_gap_detected":
            diagnostic_blockers.append("non_temperature_model_gap_detected")
    temperature_opportunity_summary = _compact_temperature_opportunity_report(
        temperature_opportunity_report
    )
    if isinstance(temperature_opportunity_summary, dict):
        temperature_conclusion = str(temperature_opportunity_summary.get("hard_conclusion") or "")
        if temperature_conclusion in {
            "temperature_opportunity_blocked_by_negative_maker",
            "temperature_opportunity_needs_maker_evidence",
        }:
            diagnostic_blockers.append(temperature_conclusion)
    temperature_execution_summary = _compact_temperature_execution_experiment_report(
        temperature_execution_experiment_report
    )
    if isinstance(temperature_execution_summary, dict):
        execution_conclusion = str(temperature_execution_summary.get("hard_conclusion") or "")
        if execution_conclusion in {
            "temperature_execution_experiment_collect_taker_paper",
            "temperature_execution_experiment_collect_offset_ladder",
            "temperature_execution_experiment_skip_negative_maker_surface",
        }:
            diagnostic_blockers.append(execution_conclusion)
    temperature_taker_validation_summary = _compact_temperature_taker_validation_report(
        temperature_taker_validation_report
    )
    current_signal_taker_validation_summary = _compact_current_signal_taker_validation_report(
        current_signal_taker_validation_report
    )
    if isinstance(current_signal_taker_validation_summary, dict):
        current_taker_conclusion = str(current_signal_taker_validation_summary.get("hard_conclusion") or "")
        if current_taker_conclusion in {
            "current_signal_taker_validation_no_markouts",
            "current_signal_taker_validation_collect_more_markouts",
            "current_signal_taker_validation_collect_required_horizons",
            "current_signal_taker_validation_negative",
            "current_signal_taker_validation_low_win_rate",
            "current_signal_taker_validation_weak_horizons",
            "current_signal_taker_validation_wait_resolved_audit",
        }:
            diagnostic_blockers.append(current_taker_conclusion)
    if isinstance(temperature_taker_validation_summary, dict):
        taker_conclusion = str(temperature_taker_validation_summary.get("hard_conclusion") or "")
        if taker_conclusion in {
            "temperature_taker_validation_no_markouts",
            "temperature_taker_validation_collect_more_markouts",
            "temperature_taker_validation_collect_required_horizons",
            "temperature_taker_validation_negative",
            "temperature_taker_validation_low_win_rate",
            "temperature_taker_validation_weak_horizons",
            "temperature_taker_validation_wait_resolved_audit",
        }:
            diagnostic_blockers.append(taker_conclusion)

    return {
        "schema_version": LIVE_READINESS_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_iso(),
        "journal_dir": str(journal_root),
        "paper_fill_schema_version": PAPER_FILL_SCHEMA_VERSION,
        "targets": {
            "min_paper_fills": MIN_PAPER_FILLS,
            "min_markouts": MIN_MARKOUTS,
            "min_resolved_audits": MIN_RESOLVED_AUDITS,
            "min_markout_win_rate": MIN_MARKOUT_WIN_RATE,
            "min_resolved_win_rate": MIN_RESOLVED_WIN_RATE,
            "min_mean_markout_cents": MIN_MEAN_MARKOUT_CENTS,
            "min_resolved_total_pnl_cents": MIN_RESOLVED_TOTAL_PNL_CENTS,
        },
        "signal": {
            "source": signal_source,
            "current_candidate_count": current_candidate_count,
            "reported_signal_count": signal_count_for_reporting,
            "historical_candidate_fill_count": historical_candidate_fill_count,
        },
        "evidence": {
            "paper_fill_count": paper_fill_count,
            "marked_count": marked_count,
            "markout_win_count": len(markout_wins),
            "markout_win_rate": markout_win_rate,
            "mean_markout_cents": mean_markout_cents,
            "resolved_count": resolved_count,
            "resolved_win_count": len(resolved_wins),
            "resolved_win_rate": resolved_win_rate,
            "resolved_total_pnl_cents": resolved_total_pnl_cents,
            "journal_summary": journal_summary,
            "markout_strata_summary": markout_strata_summary,
            "execution_calibration_summary": execution_calibration_summary,
            "maker_quote_summary": maker_quote_summary,
            "current_signal_diagnostics": current_signal_diagnostics,
            "signal_risk_filter_summary": signal_risk_filter_summary,
            "resolved_summary": resolved_summary,
            "resolved_gap_summary": resolved_gap_summary,
            "quarantine_surface_summary": quarantine_surface_summary,
            "maker_quote_blocker_calibration_summary": maker_quote_blocker_summary,
            "model_coverage_summary": model_coverage_summary,
            "temperature_opportunity_summary": temperature_opportunity_summary,
            "temperature_execution_experiment_summary": temperature_execution_summary,
            "current_signal_taker_validation_summary": current_signal_taker_validation_summary,
            "temperature_taker_validation_summary": temperature_taker_validation_summary,
            "closed_market_backfill_summary": backfill_summary,
        },
        "live_gate": live_gate,
        "live_permission": bool(live_permission),
        "live_authorization_pct": live_authorization_pct,
        "readiness_pct": readiness_pct,
        "distance_to_live_pct": round(max(0.0, 100.0 - readiness_pct), 2),
        "score_components": score_components,
        "blockers": blockers,
        "diagnostic_blockers": diagnostic_blockers,
        "hard_conclusion": (
            "可实盘" if live_gate and live_permission else "只能继续 paper"
        ),
    }


def load_signal_report(path: str | Path | None) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    nested = payload.get("signal_report")
    if isinstance(nested, dict):
        return nested
    return payload


def dump_readiness_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
