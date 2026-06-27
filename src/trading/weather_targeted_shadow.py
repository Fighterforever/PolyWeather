from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_paper_journal import (
    _safe_float,
    load_jsonl,
    stable_json_hash,
    summarize_markout_strata,
    summarize_paper_journal,
    utc_now_iso,
)
from src.trading.weather_quality_surface import bucket_type_from_label


TARGETED_SHADOW_SCHEMA_VERSION = "polyweather_weather_targeted_shadow.v1"
CURRENT_SIGNAL_TAKER_SCHEMA_VERSION = "polyweather_weather_current_signal_taker.v1"
CURRENT_SIGNAL_TAKER_VALIDATION_SCHEMA_VERSION = "polyweather_weather_current_signal_taker_validation.v1"
DEFAULT_TARGETED_SHADOW_JOURNAL_DIR = Path("data/trading/weather_targeted_shadow_paper")
DEFAULT_CURRENT_SIGNAL_TAKER_JOURNAL_DIR = Path("data/trading/weather_current_signal_taker_paper")


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _normalized_set(values: Iterable[str]) -> set[str]:
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def _target_reasons_for_item(
    item: Dict[str, Any],
    *,
    quarantine_reasons: set[str],
    bucket_types: set[str],
    near_miss_categories: set[str],
    positive_edge_only: bool,
    min_edge_percent: Optional[float],
    require_edge_floor_for_direct_reasons: bool,
) -> List[str]:
    reasons: List[str] = []
    quarantine_reason = str(item.get("quarantine_reason") or "").strip().lower()
    bucket_type = str(item.get("bucket_type") or bucket_type_from_label(item.get("bucket_label"))).strip().lower()
    edge = _safe_float(item.get("edge_percent"))
    if positive_edge_only and edge is not None and edge < 0:
        return []
    edge_floor = _safe_float(min_edge_percent)
    edge_pass = edge is not None and (edge_floor is None or edge >= edge_floor)
    direct_reason_allowed = edge_pass or not bool(require_edge_floor_for_direct_reasons)
    if quarantine_reason and quarantine_reason in quarantine_reasons and direct_reason_allowed:
        reasons.append(f"quarantine_reason:{quarantine_reason}")
    if bucket_type and bucket_type in bucket_types and direct_reason_allowed:
        reasons.append(f"bucket_type:{bucket_type}")
    categories = [
        str(category or "").strip().lower()
        for category in item.get("quarantine_non_risk_blocker_categories") or []
        if str(category or "").strip()
    ]
    if not categories and quarantine_reason.startswith("single_non_risk_blocker:"):
        categories = [quarantine_reason.split(":", 1)[1]]
    for category in categories:
        if category in near_miss_categories and edge_pass:
            reasons.append(f"near_miss_category:{category}")
    return reasons


def build_targeted_shadow_signal_report(
    signal_report: Dict[str, Any],
    *,
    quarantine_reasons: Iterable[str] = ("risk_rule_only_reject",),
    bucket_types: Iterable[str] = ("le",),
    near_miss_categories: Iterable[str] = (),
    cooldown_reasons: Iterable[str] = (),
    positive_edge_only: bool = True,
    min_edge_percent: Optional[float] = None,
    require_edge_floor_for_direct_reasons: bool = False,
    max_items: Optional[int] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a paper-only signal report containing targeted quarantine rows for extra evidence."""

    generated_at = generated_at or utc_now_iso()
    reason_set = _normalized_set(quarantine_reasons)
    bucket_type_set = _normalized_set(bucket_types)
    category_set = _normalized_set(near_miss_categories)
    cooldown_set = _normalized_set(cooldown_reasons)
    selected: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for item in signal_report.get("quarantine") or []:
        if not isinstance(item, dict):
            continue
        target_reasons = _target_reasons_for_item(
            item,
            quarantine_reasons=reason_set,
            bucket_types=bucket_type_set,
            near_miss_categories=category_set,
            positive_edge_only=bool(positive_edge_only),
            min_edge_percent=min_edge_percent,
            require_edge_floor_for_direct_reasons=bool(require_edge_floor_for_direct_reasons),
        )
        if not target_reasons:
            continue
        cooled_target_reasons = sorted(
            {
                str(reason or "").strip().lower()
                for reason in target_reasons
                if str(reason or "").strip().lower() in cooldown_set
            }
        )
        if cooled_target_reasons:
            suppressed.append(
                {
                    **item,
                    "decision": "quarantine",
                    "targeted_shadow": False,
                    "targeted_shadow_suppressed": True,
                    "targeted_shadow_reasons": target_reasons,
                    "targeted_shadow_cooldown_reasons": cooled_target_reasons,
                    "paper_only": True,
                    "live_gate_excluded": True,
                    "counts_for_live_gate": False,
                }
            )
            continue
        selected.append(
            {
                **item,
                "decision": "quarantine",
                "targeted_shadow": True,
                "targeted_shadow_reasons": target_reasons,
                "paper_only": True,
                "live_gate_excluded": True,
                "counts_for_live_gate": False,
            }
        )
    selected = sorted(selected, key=lambda row: float(row.get("score") or 0.0), reverse=True)
    if max_items is not None:
        selected = selected[: max(0, int(max_items))]
    source_snapshot_id = signal_report.get("source_snapshot_id")
    report_identity = {
        "source_snapshot_id": source_snapshot_id,
        "quarantine_reasons": sorted(reason_set),
        "bucket_types": sorted(bucket_type_set),
        "near_miss_categories": sorted(category_set),
        "cooldown_reasons": sorted(cooldown_set),
        "positive_edge_only": bool(positive_edge_only),
        "min_edge_percent": _safe_float(min_edge_percent),
        "require_edge_floor_for_direct_reasons": bool(require_edge_floor_for_direct_reasons),
        "selected_keys": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
            }
            for row in selected
        ],
    }
    return {
        "schema_version": TARGETED_SHADOW_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_snapshot_id": source_snapshot_id,
        "source_status": signal_report.get("source_status"),
        "source": signal_report.get("source"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "live_authorization_pct": 0,
        "targeted_shadow_id": stable_json_hash(report_identity, length=20),
        "target_config": {
            "quarantine_reasons": sorted(reason_set),
            "bucket_types": sorted(bucket_type_set),
            "near_miss_categories": sorted(category_set),
            "cooldown_reasons": sorted(cooldown_set),
            "positive_edge_only": bool(positive_edge_only),
            "min_edge_percent": _safe_float(min_edge_percent),
            "require_edge_floor_for_direct_reasons": bool(require_edge_floor_for_direct_reasons),
            "max_items": max_items,
        },
        "summary": {
            "candidate_count": 0,
            "watch_count": 0,
            "quarantine_count": len(selected),
            "targeted_shadow_count": len(selected),
            "targeted_shadow_suppressed_count": len(suppressed),
            "live_gate": False,
            "live_authorization_pct": 0,
            "live_blockers": [
                "targeted_shadow_paper_only",
                "live_permission_false",
            ],
        },
        "candidates": [],
        "watch": [],
        "quarantine": selected,
        "suppressed": suppressed,
    }


def _current_signal_taker_reason(row: Dict[str, Any]) -> str:
    quarantine_reason = str(row.get("quarantine_reason") or "").strip().lower()
    if quarantine_reason:
        return quarantine_reason
    non_risk = [
        str(reason or "").strip()
        for reason in row.get("quarantine_non_risk_blockers") or []
        if str(reason or "").strip()
    ]
    if len(non_risk) == 1:
        return f"single_non_risk_blocker:{non_risk[0]}"
    return "candidate_like_quarantine"


def build_current_signal_taker_probe_report(
    signal_report: Dict[str, Any],
    *,
    max_items: int = 20,
    min_edge_percent: float = 5.0,
    max_spread: float = 0.03,
    allowed_quarantine_reasons: Optional[Iterable[str]] = None,
    allowed_bucket_types: Optional[Iterable[str]] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Select current candidate-like quarantine rows for paper-only taker-cross validation."""

    generated_at = generated_at or utc_now_iso()
    allowed_reasons = (
        _normalized_set(allowed_quarantine_reasons)
        if allowed_quarantine_reasons is not None
        else set()
    )
    allowed_buckets = (
        _normalized_set(allowed_bucket_types)
        if allowed_bucket_types is not None
        else set()
    )
    rows: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for item in signal_report.get("quarantine") or []:
        if not isinstance(item, dict):
            continue
        edge = _safe_float(item.get("edge_percent"))
        spread = _safe_float(item.get("spread"))
        price = _safe_float(item.get("ask") if item.get("ask") is not None else item.get("price"))
        token_id = str(item.get("token_id") or "").strip()
        bucket_type = str(item.get("bucket_type") or bucket_type_from_label(item.get("bucket_label"))).strip().lower()
        quarantine_reason = str(item.get("quarantine_reason") or "").strip().lower()
        failure_reasons: List[str] = []
        if str(item.get("would_be_decision_without_risk_rules") or "") != "candidate":
            failure_reasons.append("not_candidate_without_risk_rules")
        if not token_id:
            failure_reasons.append("missing_token_id")
        if price is None or price <= 0:
            failure_reasons.append("missing_or_invalid_taker_price")
        if edge is None or edge < float(min_edge_percent):
            failure_reasons.append("edge_below_probe_min")
        if spread is None:
            failure_reasons.append("missing_spread")
        elif spread > float(max_spread):
            failure_reasons.append("spread_above_probe_max")
        if allowed_reasons and quarantine_reason not in allowed_reasons:
            failure_reasons.append("quarantine_reason_not_allowed")
        if allowed_buckets and bucket_type not in allowed_buckets:
            failure_reasons.append("bucket_type_not_allowed")
        if failure_reasons:
            rejected.append(
                {
                    "market_slug": item.get("market_slug"),
                    "token_id": item.get("token_id"),
                    "side": item.get("side"),
                    "quarantine_reason": item.get("quarantine_reason"),
                    "bucket_type": bucket_type,
                    "edge_percent": edge,
                    "spread": spread,
                    "failure_reasons": sorted(set(failure_reasons)),
                }
            )
            continue
        rows.append(
            {
                **item,
                "decision": "quarantine",
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_gate_excluded": True,
                "current_signal_taker_probe": True,
                "execution_mode": "taker_cross",
                "price": price,
                "ask": price,
                "quarantine_reason": _current_signal_taker_reason(item),
                "quarantine_blocker_scope": item.get("quarantine_blocker_scope") or "current_signal_probe",
                "warnings": sorted(set((item.get("warnings") or []) + ["paper_only_current_signal_taker_probe"])),
                "blockers": sorted(set((item.get("blockers") or []) + ["current_signal_taker_not_live_calibrated"])),
                "would_be_decision_without_risk_rules": "candidate",
                "would_be_decision_without_quarantine_blockers": "candidate",
            }
        )
    rows = sorted(
        rows,
        key=lambda row: (
            float(row.get("edge_percent") or 0.0),
            float(row.get("score") or 0.0),
        ),
        reverse=True,
    )[: max(0, int(max_items))]
    reason_counts: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {}
    for row in rows:
        reason = str(row.get("quarantine_reason") or "unknown")
        bucket = str(row.get("bucket_type") or bucket_type_from_label(row.get("bucket_label")) or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    identity = {
        "source_snapshot_id": signal_report.get("source_snapshot_id"),
        "min_edge_percent": float(min_edge_percent),
        "max_spread": float(max_spread),
        "allowed_quarantine_reasons": sorted(allowed_reasons),
        "allowed_bucket_types": sorted(allowed_buckets),
        "selected": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
                "price": row.get("price"),
            }
            for row in rows
        ],
    }
    if rows:
        hard_conclusion = "current_signal_taker_collect_paper"
    elif rejected:
        hard_conclusion = "current_signal_taker_no_rows_after_filters"
    else:
        hard_conclusion = "current_signal_taker_no_candidate_like_quarantine"
    return {
        "schema_version": CURRENT_SIGNAL_TAKER_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_snapshot_id": signal_report.get("source_snapshot_id"),
        "source_status": signal_report.get("source_status"),
        "source": signal_report.get("source"),
        "current_signal_taker_id": stable_json_hash(identity, length=20),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "live_authorization_pct": 0,
        "config": {
            "max_items": max(0, int(max_items)),
            "min_edge_percent": float(min_edge_percent),
            "max_spread": float(max_spread),
            "allowed_quarantine_reasons": sorted(allowed_reasons),
            "allowed_bucket_types": sorted(allowed_buckets),
        },
        "summary": {
            "candidate_count": 0,
            "watch_count": 0,
            "quarantine_count": len(rows),
            "current_signal_taker_count": len(rows),
            "rejected_probe_count": len(rejected),
            "reason_counts": [
                {"quarantine_reason": reason, "count": count}
                for reason, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "bucket_type_counts": [
                {"bucket_type": bucket, "count": count}
                for bucket, count in sorted(bucket_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "live_gate": False,
            "live_authorization_pct": 0,
            "live_blockers": [
                "current_signal_taker_paper_only",
                "taker_execution_forward_evidence_unproven",
                "live_permission_false",
            ],
        },
        "candidates": [],
        "watch": [],
        "quarantine": rows,
        "rejected_probe_rows": rejected[:20],
        "hard_conclusion": hard_conclusion,
    }


def _horizon_status_from_strata(
    all_strata: Dict[str, Any],
    *,
    required_horizons: Iterable[str],
    min_horizon_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
) -> tuple[List[Dict[str, Any]], List[str], List[str]]:
    rows = {
        str(row.get("markout_horizon") or ""): row
        for row in all_strata.get("by_horizon") or []
        if isinstance(row, dict)
    }
    status: List[Dict[str, Any]] = []
    missing: List[str] = []
    weak: List[str] = []
    for horizon in [str(value).strip() for value in required_horizons if str(value).strip()]:
        row = rows.get(horizon) or {}
        count = int(row.get("count") or 0)
        mean = _safe_float(row.get("mean_markout_cents"))
        win_rate = _safe_float(row.get("win_rate"))
        blockers: List[str] = []
        if count < max(1, int(min_horizon_count)):
            blockers.append(f"insufficient_horizon_count_{count}_of_{max(1, int(min_horizon_count))}")
            missing.append(horizon)
        if count > 0 and (mean is None or mean < float(min_mean_markout_cents)):
            blockers.append("horizon_mean_markout_below_threshold")
            weak.append(horizon)
        if count > 0 and (win_rate is None or win_rate < float(min_win_rate)):
            blockers.append("horizon_win_rate_below_threshold")
            weak.append(horizon)
        status.append(
            {
                "markout_horizon": horizon,
                "count": count,
                "mean_markout_cents": mean,
                "win_rate": win_rate,
                "blockers": sorted(set(blockers)),
            }
        )
    return status, sorted(set(missing)), sorted(set(weak))


def build_current_signal_taker_validation_report(
    *,
    journal_dir: str | Path = DEFAULT_CURRENT_SIGNAL_TAKER_JOURNAL_DIR,
    min_marked_count: int = 10,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    required_horizons: Iterable[str] = ("0-5m", "5-15m", "15-30m"),
    min_horizon_count: int = 3,
    min_resolved_count: int = 1,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    journal_root = Path(journal_dir)
    journal_summary = summarize_paper_journal(journal_root)
    latest_strata = summarize_markout_strata(journal_root, latest_only=True, min_count=1)
    all_strata = summarize_markout_strata(journal_root, latest_only=False, min_count=1)
    resolved_audits = [
        row
        for row in load_jsonl(journal_root / "resolved_audits.jsonl")
        if isinstance(row, dict) and row.get("status") == "resolved"
    ]
    marked_count = int(latest_strata.get("marked_count") or 0)
    mean_markout = _safe_float(latest_strata.get("mean_markout_cents"))
    win_rate = _safe_float(latest_strata.get("win_rate"))
    resolved_count = len(resolved_audits)
    horizon_status, missing_horizons, weak_horizons = _horizon_status_from_strata(
        all_strata,
        required_horizons=required_horizons,
        min_horizon_count=min_horizon_count,
        min_mean_markout_cents=min_mean_markout_cents,
        min_win_rate=min_win_rate,
    )
    blockers: List[str] = []
    if marked_count < max(1, int(min_marked_count)):
        blockers.append(f"insufficient_marked_count_{marked_count}_of_{max(1, int(min_marked_count))}")
    if mean_markout is None:
        blockers.append("mean_markout_missing")
    elif mean_markout < float(min_mean_markout_cents):
        blockers.append("mean_markout_below_threshold")
    if win_rate is None:
        blockers.append("win_rate_missing")
    elif win_rate < float(min_win_rate):
        blockers.append("win_rate_below_threshold")
    if missing_horizons:
        blockers.append("required_horizon_samples_missing")
    if weak_horizons:
        blockers.append("required_horizon_samples_weak")
    if resolved_count < max(0, int(min_resolved_count)):
        blockers.append(f"insufficient_resolved_count_{resolved_count}_of_{max(0, int(min_resolved_count))}")
    if marked_count == 0:
        hard_conclusion = "current_signal_taker_validation_no_markouts"
        next_action = "collect_current_signal_taker_paper"
    elif marked_count < max(1, int(min_marked_count)):
        hard_conclusion = "current_signal_taker_validation_collect_more_markouts"
        next_action = "continue_current_signal_taker_paper"
    elif mean_markout is not None and mean_markout < float(min_mean_markout_cents):
        hard_conclusion = "current_signal_taker_validation_negative"
        next_action = "keep_current_signal_taker_blocked"
    elif win_rate is not None and win_rate < float(min_win_rate):
        hard_conclusion = "current_signal_taker_validation_low_win_rate"
        next_action = "keep_current_signal_taker_blocked"
    elif weak_horizons:
        hard_conclusion = "current_signal_taker_validation_weak_horizons"
        next_action = "keep_current_signal_taker_blocked"
    elif missing_horizons:
        hard_conclusion = "current_signal_taker_validation_collect_required_horizons"
        next_action = "continue_current_signal_taker_paper"
    elif resolved_count < max(0, int(min_resolved_count)):
        hard_conclusion = "current_signal_taker_validation_wait_resolved_audit"
        next_action = "collect_resolved_audit"
    else:
        hard_conclusion = "current_signal_taker_validation_promotable_to_tiny_live_review"
        next_action = "tiny_live_review_only"
    identity = {
        "journal_dir": str(journal_root),
        "marked_count": marked_count,
        "mean_markout": mean_markout,
        "win_rate": win_rate,
        "horizon_status": horizon_status,
        "resolved_count": resolved_count,
    }
    return {
        "schema_version": CURRENT_SIGNAL_TAKER_VALIDATION_SCHEMA_VERSION,
        "generated_at": generated_at,
        "validation_id": stable_json_hash(identity, length=20),
        "journal_dir": str(journal_root),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "summary": {
            "paper_fill_count": journal_summary.get("paper_fill_count"),
            "markout_observation_count": latest_strata.get("markout_observation_count"),
            "selected_markout_count": latest_strata.get("selected_markout_count"),
            "marked_count": marked_count,
            "mean_markout_cents": mean_markout,
            "win_rate": win_rate,
            "resolved_count": resolved_count,
        },
        "horizon_status": horizon_status,
        "missing_horizons": missing_horizons,
        "weak_horizons": weak_horizons,
        "latest_strata_summary": {
            "by_horizon": latest_strata.get("by_horizon") or [],
            "by_quarantine_reason": latest_strata.get("by_quarantine_reason") or [],
            "by_bucket_type": latest_strata.get("by_bucket_type") or [],
            "by_entry_spread_bucket": latest_strata.get("by_entry_spread_bucket") or [],
            "do_not_live_rules": latest_strata.get("do_not_live_rules") or [],
        },
        "all_observation_horizon_summary": all_strata.get("by_horizon") or [],
        "blockers": blockers,
        "next_action": next_action,
        "hard_conclusion": hard_conclusion,
    }


def dump_targeted_shadow_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def _latest_by_id(records: Iterable[Dict[str, Any]], key_field: str) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = str(record.get(key_field) or f"row:{index}")
        latest[key] = record
    return latest


def _targeted_reasons_from_record(record: Dict[str, Any]) -> List[str]:
    raw_reasons = record.get("targeted_shadow_reasons")
    if isinstance(raw_reasons, list):
        reasons = [str(reason or "").strip().lower() for reason in raw_reasons if str(reason or "").strip()]
        if reasons:
            return reasons
    inferred: List[str] = []
    quarantine_reason = str(record.get("quarantine_reason") or "").strip().lower()
    if quarantine_reason == "risk_rule_only_reject":
        inferred.append("quarantine_reason:risk_rule_only_reject")
    bucket_type = str(record.get("bucket_type") or bucket_type_from_label(record.get("bucket_label"))).strip().lower()
    if bucket_type == "le":
        inferred.append("bucket_type:le")
    return inferred or ["unknown"]


def _empty_target_group(reason: str) -> Dict[str, Any]:
    return {
        "targeted_shadow_reason": reason,
        "fill_count": 0,
        "marked_count": 0,
        "_markout_values": [],
        "quote_markout_count": 0,
        "maker_inferred_fill_count": 0,
        "_maker_values": [],
        "_missed_taker_values": [],
    }


def _finalize_target_group(
    group: Dict[str, Any],
    *,
    min_marked_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
    min_maker_inferred_fills: int,
    min_maker_mean_markout_cents: float,
) -> Dict[str, Any]:
    markout_values = [float(value) for value in group.pop("_markout_values", [])]
    maker_values = [float(value) for value in group.pop("_maker_values", [])]
    missed_values = [float(value) for value in group.pop("_missed_taker_values", [])]
    mean_markout = _mean(markout_values)
    win_rate = _ratio(len([value for value in markout_values if value > 0.0]), len(markout_values))
    mean_maker = _mean(maker_values)
    failure_reasons: List[str] = []
    marked_count = int(group.get("marked_count") or 0)
    if marked_count < max(1, int(min_marked_count)):
        failure_reasons.append(f"insufficient_marked_count_{marked_count}_of_{max(1, int(min_marked_count))}")
    if mean_markout is None:
        failure_reasons.append("mean_markout_missing")
    elif mean_markout < float(min_mean_markout_cents):
        failure_reasons.append("mean_markout_below_threshold")
    if win_rate is None:
        failure_reasons.append("win_rate_missing")
    elif win_rate < float(min_win_rate):
        failure_reasons.append("win_rate_below_threshold")
    maker_inferred = int(group.get("maker_inferred_fill_count") or 0)
    if max(0, int(min_maker_inferred_fills)) > 0:
        if maker_inferred < int(min_maker_inferred_fills):
            failure_reasons.append(
                f"insufficient_maker_inferred_fills_{maker_inferred}_of_{int(min_maker_inferred_fills)}"
            )
        elif mean_maker is None:
            failure_reasons.append("maker_mean_markout_missing")
        elif mean_maker < float(min_maker_mean_markout_cents):
            failure_reasons.append("maker_mean_markout_below_threshold")
    if not failure_reasons:
        action = "promote_to_formal_paper_review"
    elif any(reason.startswith("insufficient_") for reason in failure_reasons):
        action = "collect_more_targeted_shadow_evidence"
    elif "maker_mean_markout_below_threshold" in failure_reasons:
        action = "keep_shadow_maker_adverse"
    else:
        action = "keep_shadow_negative_forward_edge"
    return {
        **group,
        "win_rate": win_rate,
        "mean_markout_cents": mean_markout,
        "min_markout_cents": round(min(markout_values), 6) if markout_values else None,
        "max_markout_cents": round(max(markout_values), 6) if markout_values else None,
        "maker_markout_win_rate": _ratio(len([value for value in maker_values if value > 0.0]), len(maker_values)),
        "mean_maker_markout_cents": mean_maker,
        "mean_missed_taker_markout_cents": _mean(missed_values),
        "failure_reasons": failure_reasons,
        "action": action,
        "counts_for_live_gate": False,
    }


def build_targeted_shadow_validation_report(
    *,
    journal_dir: str | Path = DEFAULT_TARGETED_SHADOW_JOURNAL_DIR,
    min_marked_count: int = 5,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    min_maker_inferred_fills: int = 3,
    min_maker_mean_markout_cents: float = 0.0,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    root = Path(journal_dir)
    fills = [row for row in load_jsonl(root / "paper_fills.jsonl") if isinstance(row, dict)]
    fills_by_id = {str(fill.get("fill_id") or ""): fill for fill in fills}
    quotes_by_id = {
        str(quote.get("quote_id") or ""): quote
        for quote in load_jsonl(root / "maker_quotes.jsonl")
        if isinstance(quote, dict)
    }
    latest_markouts = _latest_by_id(load_jsonl(root / "markouts.jsonl"), "fill_id")
    latest_quote_markouts = _latest_by_id(load_jsonl(root / "maker_quote_markouts.jsonl"), "quote_id")
    groups: Dict[str, Dict[str, Any]] = {}

    for fill in fills:
        for reason in _targeted_reasons_from_record(fill):
            group = groups.setdefault(reason, _empty_target_group(reason))
            group["fill_count"] += 1

    for fill_id, markout in latest_markouts.items():
        fill = fills_by_id.get(str(fill_id) or "") or {}
        merged = {**fill, **markout}
        if markout.get("status") != "marked":
            continue
        markout_value = _safe_float(markout.get("markout_cents"))
        if markout_value is None:
            continue
        for reason in _targeted_reasons_from_record(merged):
            group = groups.setdefault(reason, _empty_target_group(reason))
            group["marked_count"] += 1
            group["_markout_values"].append(float(markout_value))

    for quote_id, markout in latest_quote_markouts.items():
        quote = quotes_by_id.get(str(quote_id) or "") or {}
        fill = fills_by_id.get(str(markout.get("fill_id") or quote.get("fill_id") or "")) or {}
        merged = {**fill, **quote, **markout}
        for reason in _targeted_reasons_from_record(merged):
            group = groups.setdefault(reason, _empty_target_group(reason))
            group["quote_markout_count"] += 1
            if markout.get("status") == "inferred_filled":
                group["maker_inferred_fill_count"] += 1
                maker_value = _safe_float(markout.get("maker_markout_cents"))
                if maker_value is not None:
                    group["_maker_values"].append(float(maker_value))
            elif markout.get("status") == "resting_unfilled":
                missed_value = _safe_float(markout.get("missed_taker_markout_cents"))
                if missed_value is not None:
                    group["_missed_taker_values"].append(float(missed_value))

    finalized = [
        _finalize_target_group(
            group,
            min_marked_count=min_marked_count,
            min_mean_markout_cents=min_mean_markout_cents,
            min_win_rate=min_win_rate,
            min_maker_inferred_fills=min_maker_inferred_fills,
            min_maker_mean_markout_cents=min_maker_mean_markout_cents,
        )
        for group in groups.values()
    ]
    finalized = sorted(
        finalized,
        key=lambda row: (
            0 if row.get("action") == "promote_to_formal_paper_review" else 1,
            -float(row.get("mean_markout_cents") or -9999.0),
            -float(row.get("win_rate") or 0.0),
            -int(row.get("marked_count") or 0),
        ),
    )
    ready = [row for row in finalized if row.get("action") == "promote_to_formal_paper_review"]
    marked_values = [
        _safe_float(row.get("markout_cents"))
        for row in latest_markouts.values()
        if row.get("status") == "marked" and _safe_float(row.get("markout_cents")) is not None
    ]
    marked_numbers = [float(value) for value in marked_values if value is not None]
    return {
        "schema_version": TARGETED_SHADOW_SCHEMA_VERSION,
        "report_type": "targeted_shadow_validation",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "journal_dir": str(root),
        "thresholds": {
            "min_marked_count": max(1, int(min_marked_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
            "min_maker_inferred_fills": max(0, int(min_maker_inferred_fills)),
            "min_maker_mean_markout_cents": float(min_maker_mean_markout_cents),
        },
        "evidence": {
            "fill_count": len(fills),
            "marked_count": len(marked_numbers),
            "mean_markout_cents": _mean(marked_numbers),
            "win_rate": _ratio(len([value for value in marked_numbers if value > 0.0]), len(marked_numbers)),
            "maker_quote_count": len(quotes_by_id),
            "maker_quote_markout_count": len(latest_quote_markouts),
            "maker_inferred_fill_count": len(
                [row for row in latest_quote_markouts.values() if row.get("status") == "inferred_filled"]
            ),
        },
        "ready_group_count": len(ready),
        "ready_groups": ready[:10],
        "groups": finalized,
        "hard_conclusion": (
            "targeted_shadow_ready_for_formal_paper_review"
            if ready
            else ("targeted_shadow_collect_more_or_reject" if finalized else "no_targeted_shadow_evidence")
        ),
    }


def build_targeted_shadow_cooldown_report(
    validation_report: Dict[str, Any],
    *,
    min_marked_count: int = 2,
    min_mean_markout_cents: float = 0.0,
    max_win_rate: float = 0.5,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Return targeted-shadow reasons that should pause because recent paper evidence is negative."""

    generated_at = generated_at or utc_now_iso()
    cooldown_groups: List[Dict[str, Any]] = []
    min_count = max(1, int(min_marked_count))
    mean_floor = float(min_mean_markout_cents)
    win_ceiling = float(max_win_rate)
    for group in validation_report.get("groups") or []:
        if not isinstance(group, dict):
            continue
        reason = str(group.get("targeted_shadow_reason") or "").strip().lower()
        if not reason:
            continue
        marked_count = int(group.get("marked_count") or 0)
        mean_markout = _safe_float(group.get("mean_markout_cents"))
        win_rate = _safe_float(group.get("win_rate"))
        if marked_count < min_count or mean_markout is None or win_rate is None:
            continue
        if mean_markout < mean_floor and win_rate <= win_ceiling:
            cooldown_groups.append(
                {
                    "targeted_shadow_reason": reason,
                    "marked_count": marked_count,
                    "mean_markout_cents": mean_markout,
                    "win_rate": win_rate,
                    "action": "cooldown_targeted_shadow_reason",
                    "reason": "negative_forward_markout",
                    "counts_for_live_gate": False,
                }
            )
    cooldown_groups = sorted(
        cooldown_groups,
        key=lambda row: (
            float(row.get("mean_markout_cents") or 0.0),
            float(row.get("win_rate") or 0.0),
            -int(row.get("marked_count") or 0),
        ),
    )
    cooldown_reasons = [row["targeted_shadow_reason"] for row in cooldown_groups]
    return {
        "schema_version": TARGETED_SHADOW_SCHEMA_VERSION,
        "report_type": "targeted_shadow_cooldown",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "thresholds": {
            "min_marked_count": min_count,
            "min_mean_markout_cents": mean_floor,
            "max_win_rate": win_ceiling,
        },
        "source_validation_hard_conclusion": validation_report.get("hard_conclusion"),
        "cooldown_group_count": len(cooldown_groups),
        "cooldown_reasons": cooldown_reasons,
        "groups": cooldown_groups,
        "hard_conclusion": (
            "targeted_shadow_cooldown_active"
            if cooldown_groups
            else "targeted_shadow_no_cooldown"
        ),
    }
