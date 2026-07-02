from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_maker_quote_journal import summarize_maker_quote_strata
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR, summarize_markout_strata, utc_now_iso
from src.trading.weather_signal_risk_filter import build_risk_rules_from_journal, normalize_risk_rules


QUARANTINE_VALIDATION_SCHEMA_VERSION = "polyweather_weather_quarantine_validation.v1"
DEFAULT_QUARANTINE_JOURNAL_DIR = Path("data/trading/weather_quarantine_paper")
RISK_RULE_QUARANTINE_REASONS = ("risk_rule_only_reject",)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_dimensions(dimensions: Any) -> Dict[str, str]:
    if not isinstance(dimensions, dict):
        return {}
    normalized: Dict[str, str] = {}
    for key, value in dimensions.items():
        key_text = str(key or "").strip()
        value_text = str(value or "").strip().lower()
        if key_text and value_text:
            normalized[key_text] = value_text
    return normalized


def _row_dimensions(row: Dict[str, Any], dimensions: Dict[str, str]) -> Dict[str, str]:
    return {
        key: str(row.get(key) or "").strip().lower()
        for key in dimensions
    }


def _find_evidence_row(
    groups: Dict[str, List[Dict[str, Any]]],
    *,
    group: str,
    dimensions: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    for row in groups.get(group) or []:
        if _row_dimensions(row, dimensions) == dimensions:
            return row
    return None


def _markout_groups(summary: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "by_horizon": list(summary.get("by_horizon") or []),
        "by_city": list(summary.get("by_city") or []),
        "by_bucket_type": list(summary.get("by_bucket_type") or []),
        "by_entry_price_bucket": list(summary.get("by_entry_price_bucket") or []),
        "by_entry_spread_bucket": list(summary.get("by_entry_spread_bucket") or []),
        "by_horizon_and_spread": list(summary.get("by_horizon_and_spread") or []),
    }


def _maker_groups(summary: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "maker_quote_by_city": list(summary.get("by_city") or []),
        "maker_quote_by_market_family": list(summary.get("by_market_family") or []),
        "maker_quote_by_bucket_type": list(summary.get("by_bucket_type") or []),
        "maker_quote_by_quote_price_bucket": list(summary.get("by_quote_price_bucket") or []),
        "maker_quote_by_quote_strategy": list(summary.get("by_quote_strategy") or []),
        "maker_quote_by_entry_spread_bucket": list(summary.get("by_entry_spread_bucket") or []),
        "maker_quote_by_time_to_expiry": list(summary.get("by_time_to_expiry") or []),
        "maker_quote_by_city_and_bucket_type": list(summary.get("by_city_and_bucket_type") or []),
        "maker_quote_by_time_to_expiry_and_spread": list(summary.get("by_time_to_expiry_and_spread") or []),
        "maker_quote_by_strategy_and_spread": list(summary.get("by_strategy_and_spread") or []),
        "maker_quote_by_strategy_and_time_to_expiry": list(summary.get("by_strategy_and_time_to_expiry") or []),
    }


def _evaluate_markout_evidence(
    row: Optional[Dict[str, Any]],
    *,
    min_unlock_count: int,
    min_unlock_mean_markout_cents: float,
    min_unlock_win_rate: float,
) -> Tuple[str, str]:
    if not row:
        return "no_quarantine_evidence", "No matching quarantine markout stratum yet."
    count = _safe_int(row.get("count"))
    mean_markout = _safe_float(row.get("mean_markout_cents"))
    win_rate = _safe_float(row.get("win_rate"))
    if count < max(1, int(min_unlock_count)):
        return "collect_more_quarantine", "Matching quarantine stratum exists but sample count is below unlock threshold."
    if mean_markout is None or win_rate is None:
        return "collect_more_quarantine", "Matching quarantine stratum lacks complete markout metrics."
    if mean_markout >= min_unlock_mean_markout_cents and win_rate >= min_unlock_win_rate:
        return "eligible_for_unlock_review", "Quarantine evidence is positive enough to review this rule for paper-only unlock."
    return "validated_block", "Quarantine evidence still supports keeping this rule blocked."


def _evaluate_maker_evidence(
    row: Optional[Dict[str, Any]],
    *,
    min_unlock_count: int,
    min_maker_inferred_fills: int,
    min_unlock_mean_markout_cents: float,
    min_unlock_win_rate: float,
) -> Tuple[str, str]:
    if not row:
        return "no_quarantine_evidence", "No matching quarantine maker-quote stratum yet."
    count = _safe_int(row.get("count"))
    inferred = _safe_int(row.get("inferred_fill_count"))
    maker_mean = _safe_float(row.get("mean_maker_markout_cents"))
    maker_win_rate = _safe_float(row.get("maker_markout_win_rate"))
    fill_rate = _safe_float(row.get("fill_inference_rate"))
    missed_taker_mean = _safe_float(row.get("mean_missed_taker_markout_cents"))
    if count < max(1, int(min_unlock_count)):
        return "collect_more_quarantine", "Matching maker-quote stratum exists but quote count is below unlock threshold."
    if inferred < max(1, int(min_maker_inferred_fills)):
        if fill_rate == 0.0:
            if missed_taker_mean is not None and missed_taker_mean <= 0.0:
                return (
                    "validated_block_no_fill_protected",
                    "Maker quote avoided taker entries that currently mark out negative, but has no inferred fills.",
                )
            if missed_taker_mean is not None and missed_taker_mean > 0.0:
                return (
                    "validated_block_no_fill_missed_upside",
                    "Maker quote had no inferred fills and would have missed positive taker markouts.",
                )
            return "validated_block_no_fill", "Maker quote has enough observations but no inferred fills."
        return "collect_more_quarantine", "Maker quote needs more inferred fills before unlock review."
    if maker_mean is None or maker_win_rate is None:
        return "collect_more_quarantine", "Maker quote has inferred fills but lacks complete maker markout metrics."
    if maker_mean >= min_unlock_mean_markout_cents and maker_win_rate >= min_unlock_win_rate:
        return "eligible_for_unlock_review", "Maker quote quarantine evidence is positive enough to review this rule for paper-only unlock."
    return "validated_block", "Maker quote quarantine evidence still supports keeping this rule blocked."


def _rule_validation_record(
    rule: Dict[str, Any],
    *,
    markout_groups: Dict[str, List[Dict[str, Any]]],
    maker_groups: Dict[str, List[Dict[str, Any]]],
    min_unlock_count: int,
    min_maker_inferred_fills: int,
    min_unlock_mean_markout_cents: float,
    min_unlock_win_rate: float,
) -> Dict[str, Any]:
    group = str(rule.get("group") or "").strip()
    dimensions = _normalize_dimensions(rule.get("dimensions"))
    source = "maker_quote" if group.startswith("maker_quote_") else "markout"
    groups = maker_groups if source == "maker_quote" else markout_groups
    evidence = _find_evidence_row(groups, group=group, dimensions=dimensions)
    if source == "maker_quote":
        status, reason = _evaluate_maker_evidence(
            evidence,
            min_unlock_count=min_unlock_count,
            min_maker_inferred_fills=min_maker_inferred_fills,
            min_unlock_mean_markout_cents=min_unlock_mean_markout_cents,
            min_unlock_win_rate=min_unlock_win_rate,
        )
    else:
        status, reason = _evaluate_markout_evidence(
            evidence,
            min_unlock_count=min_unlock_count,
            min_unlock_mean_markout_cents=min_unlock_mean_markout_cents,
            min_unlock_win_rate=min_unlock_win_rate,
        )
    return {
        "group": group,
        "dimensions": dimensions,
        "source": source,
        "rule_count": rule.get("count"),
        "rule_mean_markout_cents": rule.get("mean_markout_cents"),
        "rule_win_rate": rule.get("win_rate"),
        "status": status,
        "reason": reason,
        "quarantine_evidence": evidence,
    }


def build_quarantine_validation_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    quarantine_journal_dir: str | Path = DEFAULT_QUARANTINE_JOURNAL_DIR,
    min_unlock_count: int = 5,
    min_maker_inferred_fills: int = 3,
    min_unlock_mean_markout_cents: float = 0.0,
    min_unlock_win_rate: float = 0.55,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    risk_rules_payload = build_risk_rules_from_journal(journal_dir=paper_journal_dir)
    rules = normalize_risk_rules(risk_rules_payload.get("rules") or [])
    quarantine_markout_summary = summarize_markout_strata(
        quarantine_journal_dir,
        latest_only=True,
        min_count=1,
        quarantine_reasons=RISK_RULE_QUARANTINE_REASONS,
    )
    quarantine_maker_summary = summarize_maker_quote_strata(
        quarantine_journal_dir,
        latest_only=True,
        min_count=1,
        quarantine_reasons=RISK_RULE_QUARANTINE_REASONS,
    )
    validations = [
        _rule_validation_record(
            rule,
            markout_groups=_markout_groups(quarantine_markout_summary),
            maker_groups=_maker_groups(quarantine_maker_summary),
            min_unlock_count=min_unlock_count,
            min_maker_inferred_fills=min_maker_inferred_fills,
            min_unlock_mean_markout_cents=min_unlock_mean_markout_cents,
            min_unlock_win_rate=min_unlock_win_rate,
        )
        for rule in rules
    ]
    status_counts: Dict[str, int] = {}
    for record in validations:
        status = str(record.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    unlock_candidates = [
        record
        for record in validations
        if record.get("status") == "eligible_for_unlock_review"
    ]
    return {
        "schema_version": QUARANTINE_VALIDATION_SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_journal_dir": str(paper_journal_dir),
        "quarantine_journal_dir": str(quarantine_journal_dir),
        "paper_only": True,
        "counts_for_live_gate": False,
        "validation_quarantine_reasons": list(RISK_RULE_QUARANTINE_REASONS),
        "thresholds": {
            "min_unlock_count": max(1, int(min_unlock_count)),
            "min_maker_inferred_fills": max(1, int(min_maker_inferred_fills)),
            "min_unlock_mean_markout_cents": float(min_unlock_mean_markout_cents),
            "min_unlock_win_rate": float(min_unlock_win_rate),
        },
        "risk_rule_count": len(rules),
        "status_counts": dict(sorted(status_counts.items())),
        "maker_only_tradeoff": {
            "protected_no_fill_rule_count": status_counts.get("validated_block_no_fill_protected", 0),
            "missed_upside_no_fill_rule_count": status_counts.get("validated_block_no_fill_missed_upside", 0),
            "plain_no_fill_rule_count": status_counts.get("validated_block_no_fill", 0),
            "adverse_selection_rule_count": len(
                [
                    record
                    for record in validations
                    if record.get("source") == "maker_quote"
                    and record.get("status") == "validated_block"
                ]
            ),
            "eligible_maker_unlock_count": len(
                [
                    record
                    for record in validations
                    if record.get("source") == "maker_quote"
                    and record.get("status") == "eligible_for_unlock_review"
                ]
            ),
        },
        "unlock_candidate_count": len(unlock_candidates),
        "hard_conclusion": (
            "review_paper_only_unlock_candidates"
            if unlock_candidates
            else "keep_current_risk_rules"
        ),
        "quarantine_markout_summary": {
            "marked_count": quarantine_markout_summary.get("marked_count"),
            "mean_markout_cents": quarantine_markout_summary.get("mean_markout_cents"),
            "win_rate": quarantine_markout_summary.get("win_rate"),
        },
        "quarantine_maker_quote_summary": {
            "quote_markout_count": quarantine_maker_summary.get("quote_markout_count"),
            "inferred_fill_count": quarantine_maker_summary.get("inferred_fill_count"),
            "fill_inference_rate": quarantine_maker_summary.get("fill_inference_rate"),
            "mean_maker_markout_cents": quarantine_maker_summary.get("mean_maker_markout_cents"),
            "maker_markout_win_rate": quarantine_maker_summary.get("maker_markout_win_rate"),
        },
        "unlock_candidates": unlock_candidates,
        "validations": validations,
    }


def dump_quarantine_validation_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
