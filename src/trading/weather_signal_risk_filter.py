from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_paper_journal import (
    _parse_utc_iso,
    _market_family_from_record,
    edge_bucket,
    price_bucket,
    spread_bucket,
    summarize_markout_strata,
    utc_now_iso,
)


RISK_RULE_SCHEMA_VERSION = "polyweather_weather_signal_risk_rules.v1"
SUPPORTED_RISK_DIMENSION_KEYS = {
    "market_family",
    "side",
    "city",
    "bucket_type",
    "entry_price_bucket",
    "maker_quote_price_bucket",
    "entry_spread_bucket",
    "entry_edge_bucket",
    "time_to_expiry_bucket",
    "fine_time_to_expiry_bucket",
}
DEFAULT_QUARANTINE_SURFACE_RISK_GROUPS = {
    "by_city",
    "by_bucket_type",
    "by_city_and_bucket_type",
    "by_side_and_bucket_type",
    "by_city_and_side",
    "by_entry_price_bucket",
    "by_entry_spread_bucket",
}


def _bucket_type(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(">="):
        return "ge"
    if text.startswith("<="):
        return "le"
    if text.startswith("="):
        return "eq"
    if "-" in text:
        return "range"
    return "unknown"


def time_to_expiry_bucket(end_date: Any, *, now: Optional[Any] = None) -> str:
    end_dt = _parse_utc_iso(end_date)
    now_dt = _parse_utc_iso(now or utc_now_iso())
    if end_dt is None or now_dt is None:
        return "missing"
    seconds = (end_dt - now_dt).total_seconds()
    if seconds <= 0:
        return "expired"
    hours = seconds / 3600.0
    if hours <= 6:
        return "<=6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 48:
        return "1-2d"
    if hours <= 168:
        return "2-7d"
    return ">7d"


def fine_time_to_expiry_bucket(end_date: Any, *, now: Optional[Any] = None) -> str:
    end_dt = _parse_utc_iso(end_date)
    now_dt = _parse_utc_iso(now or utc_now_iso())
    if end_dt is None or now_dt is None:
        return "missing"
    seconds = (end_dt - now_dt).total_seconds()
    if seconds <= 0:
        return "expired"
    hours = seconds / 3600.0
    if hours <= 3:
        return "<=3h"
    if hours <= 6:
        return "3-6h"
    if hours <= 12:
        return "6-12h"
    if hours <= 24:
        return "12-24h"
    if hours <= 36:
        return "24-36h"
    if hours <= 48:
        return "36-48h"
    if hours <= 96:
        return "2-4d"
    if hours <= 168:
        return "4-7d"
    return ">7d"


def row_risk_dimensions(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "market_family": _market_family_from_record(row),
        "side": str(row.get("side") or "unknown").strip().lower() or "unknown",
        "city": str(row.get("city") or "unknown").strip().lower() or "unknown",
        "bucket_type": _bucket_type(row.get("bucket_label")),
        "entry_price_bucket": price_bucket(row.get("price")),
        "maker_quote_price_bucket": price_bucket(row.get("bid") or row.get("best_bid")),
        "entry_spread_bucket": spread_bucket(row.get("spread")),
        "entry_edge_bucket": edge_bucket(row.get("edge_percent")),
        "time_to_expiry_bucket": time_to_expiry_bucket(row.get("end_date")),
        "fine_time_to_expiry_bucket": fine_time_to_expiry_bucket(row.get("end_date")),
    }


def normalize_risk_rules(rules: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("action") != "do_not_live_until_positive_markout":
            continue
        dimensions = rule.get("dimensions")
        if not isinstance(dimensions, dict) or not dimensions:
            continue
        normalized_dimensions = {
            str(key): str(value).strip().lower()
            for key, value in dimensions.items()
            if str(key).strip() and str(value).strip()
        }
        if not normalized_dimensions:
            continue
        normalized.append(
            {
                "action": "do_not_live_until_positive_markout",
                "group": rule.get("group"),
                "dimensions": normalized_dimensions,
                "count": rule.get("count"),
                "win_rate": rule.get("win_rate"),
                "mean_markout_cents": rule.get("mean_markout_cents"),
                "source": rule.get("source"),
                "reason": rule.get("reason"),
                "inferred_fill_count": rule.get("inferred_fill_count"),
                "fill_inference_rate": rule.get("fill_inference_rate"),
                "mean_maker_markout_cents": rule.get("mean_maker_markout_cents"),
            }
        )
    return normalized


def _split_rules_by_supported_dimensions(
    rules: Iterable[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    actionable: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    for rule in normalize_risk_rules(rules):
        dimensions = rule.get("dimensions") if isinstance(rule.get("dimensions"), dict) else {}
        unsupported_keys = sorted(
            key
            for key in dimensions
            if key not in SUPPORTED_RISK_DIMENSION_KEYS
        )
        if unsupported_keys:
            unsupported.append({**rule, "unsupported_dimension_keys": unsupported_keys})
            continue
        actionable.append(rule)
    return actionable, unsupported


def risk_rules_for_mode(
    rules: Iterable[Dict[str, Any]],
    *,
    mode: str = "live",
) -> List[Dict[str, Any]]:
    normalized = normalize_risk_rules(rules)
    if mode == "live":
        return normalized
    if mode != "exploration":
        return []

    selected: List[Dict[str, Any]] = []
    for rule in normalized:
        dimensions = rule.get("dimensions") if isinstance(rule.get("dimensions"), dict) else {}
        group = str(rule.get("group") or "").strip()
        if len(dimensions) >= 2:
            selected.append(rule)
            continue
        if group == "by_entry_spread_bucket":
            selected.append(rule)
    return selected


def matching_risk_rules(
    row: Dict[str, Any],
    rules: Iterable[Dict[str, Any]],
    *,
    mode: str = "live",
) -> List[Dict[str, Any]]:
    dimensions = row_risk_dimensions(row)
    matches: List[Dict[str, Any]] = []
    for rule in risk_rules_for_mode(rules, mode=mode):
        rule_dimensions = rule.get("dimensions") if isinstance(rule.get("dimensions"), dict) else {}
        if all(dimensions.get(str(key)) == str(value).lower() for key, value in rule_dimensions.items()):
            matches.append(rule)
    return matches


def risk_blockers_for_row(
    row: Dict[str, Any],
    rules: Iterable[Dict[str, Any]],
    *,
    mode: str = "live",
) -> List[str]:
    blockers: List[str] = []
    seen: set[str] = set()
    for rule in matching_risk_rules(row, rules, mode=mode):
        group = str(rule.get("group") or "markout").strip() or "markout"
        dimensions = rule.get("dimensions") if isinstance(rule.get("dimensions"), dict) else {}
        dimension_text = ",".join(f"{key}={value}" for key, value in sorted(dimensions.items()))
        blocker = f"negative_markout_rule:{group}:{dimension_text}"
        if blocker in seen:
            continue
        seen.add(blocker)
        blockers.append(blocker)
    return blockers


def load_risk_rules_from_markout_strata(path: str | Path | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    return normalize_risk_rules(payload.get("do_not_live_rules") or [])


def build_risk_rules_from_journal(
    *,
    journal_dir: str | Path,
    min_count: int = 1,
    include_maker_quote_rules: bool = True,
    include_quarantine_surface_rules: bool = False,
    quarantine_journal_dir: str | Path | None = None,
    quarantine_surface_min_decision_count: int = 5,
    quarantine_surface_min_promote_count: int = 5,
    quarantine_surface_min_mean_markout_cents: float = 0.0,
    quarantine_surface_min_win_rate: float = 0.55,
    quarantine_surface_min_maker_quote_count: int = 0,
    quarantine_surface_min_maker_mean_markout_cents: float = 0.0,
    quarantine_surface_allowed_groups: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    summary = summarize_markout_strata(journal_dir, latest_only=True, min_count=min_count)
    raw_rules = list(summary.get("do_not_live_rules") or [])
    maker_quote_summary: Dict[str, Any] = {}
    quarantine_surface_rules_payload: Dict[str, Any] = {}
    if include_maker_quote_rules:
        from src.trading.weather_maker_quote_journal import summarize_maker_quote_strata

        maker_quote_summary = summarize_maker_quote_strata(
            journal_dir,
            latest_only=True,
            min_count=min_count,
        )
        raw_rules.extend(maker_quote_summary.get("do_not_live_rules") or [])
    if include_quarantine_surface_rules:
        quarantine_surface_rules_payload = build_risk_rules_from_quarantine_surface(
            paper_journal_dir=journal_dir,
            quarantine_journal_dir=quarantine_journal_dir,
            min_decision_count=quarantine_surface_min_decision_count,
            min_promote_count=quarantine_surface_min_promote_count,
            min_mean_markout_cents=quarantine_surface_min_mean_markout_cents,
            min_win_rate=quarantine_surface_min_win_rate,
            min_maker_quote_count=quarantine_surface_min_maker_quote_count,
            min_maker_mean_markout_cents=quarantine_surface_min_maker_mean_markout_cents,
            allowed_groups=quarantine_surface_allowed_groups,
        )
        raw_rules.extend(quarantine_surface_rules_payload.get("rules") or [])
    rules, unsupported_rules = _split_rules_by_supported_dimensions(raw_rules)
    return {
        "schema_version": RISK_RULE_SCHEMA_VERSION,
        "journal_dir": str(journal_dir),
        "source": (
            "markout_maker_quote_and_quarantine_surface_strata"
            if include_maker_quote_rules and include_quarantine_surface_rules
            else (
                "markout_and_quarantine_surface_strata"
                if include_quarantine_surface_rules
                else ("markout_and_maker_quote_strata" if include_maker_quote_rules else "markout_strata")
            )
        ),
        "min_count": max(1, int(min_count)),
        "raw_rule_count": len(normalize_risk_rules(raw_rules)),
        "rule_count": len(rules),
        "unsupported_rule_count": len(unsupported_rules),
        "unsupported_rule_groups": sorted(
            {
                str(rule.get("group") or "unknown")
                for rule in unsupported_rules
            }
        ),
        "unsupported_rules_sample": unsupported_rules[:10],
        "rules": rules,
        "markout_summary": {
            "marked_count": summary.get("marked_count"),
            "mean_markout_cents": summary.get("mean_markout_cents"),
            "win_rate": summary.get("win_rate"),
        },
        "maker_quote_summary": {
            "quote_markout_count": maker_quote_summary.get("quote_markout_count"),
            "inferred_fill_count": maker_quote_summary.get("inferred_fill_count"),
            "fill_inference_rate": maker_quote_summary.get("fill_inference_rate"),
            "mean_maker_markout_cents": maker_quote_summary.get("mean_maker_markout_cents"),
            "maker_markout_win_rate": maker_quote_summary.get("maker_markout_win_rate"),
        } if maker_quote_summary else None,
        "quarantine_surface_summary": {
            "hard_conclusion": quarantine_surface_rules_payload.get("surface_hard_conclusion"),
            "surface_evidence": quarantine_surface_rules_payload.get("surface_evidence"),
            "surface_action_counts": quarantine_surface_rules_payload.get("surface_action_counts"),
            "raw_cooldown_group_count": quarantine_surface_rules_payload.get("raw_cooldown_group_count"),
            "rule_count": quarantine_surface_rules_payload.get("rule_count"),
            "unsupported_rule_count": quarantine_surface_rules_payload.get("unsupported_rule_count"),
            "allowed_groups": quarantine_surface_rules_payload.get("allowed_groups"),
        } if quarantine_surface_rules_payload else None,
    }


def _quarantine_surface_rule_from_group(row: Dict[str, Any]) -> Dict[str, Any]:
    dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
    normalized_dimensions = {
        str(key): str(value).strip().lower()
        for key, value in dimensions.items()
        if str(key).strip() and str(value).strip() and str(value).strip().lower() != "unknown"
    }
    return {
        "action": "do_not_live_until_positive_markout",
        "group": row.get("group_name"),
        "dimensions": normalized_dimensions,
        "count": row.get("taker_marked_count"),
        "win_rate": row.get("taker_win_rate"),
        "mean_markout_cents": row.get("mean_taker_markout_cents"),
        "source": "quarantine_surface",
        "reason": "quarantine_surface_cooldown",
        "inferred_fill_count": row.get("maker_inferred_fill_count"),
        "fill_inference_rate": row.get("maker_fill_inference_rate"),
        "mean_maker_markout_cents": row.get("mean_maker_markout_cents"),
    }


def build_risk_rules_from_quarantine_surface(
    *,
    paper_journal_dir: str | Path,
    quarantine_journal_dir: str | Path | None = None,
    min_decision_count: int = 5,
    min_promote_count: int = 5,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    min_maker_quote_count: int = 0,
    min_maker_mean_markout_cents: float = 0.0,
    allowed_groups: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    from src.trading.weather_quarantine_surface import build_quarantine_surface_report
    from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR

    allowed = {
        str(group or "").strip()
        for group in (allowed_groups or DEFAULT_QUARANTINE_SURFACE_RISK_GROUPS)
        if str(group or "").strip()
    }
    report = build_quarantine_surface_report(
        paper_journal_dir=paper_journal_dir,
        quarantine_journal_dir=quarantine_journal_dir or DEFAULT_QUARANTINE_JOURNAL_DIR,
        min_decision_count=min_decision_count,
        min_promote_count=min_promote_count,
        min_mean_markout_cents=min_mean_markout_cents,
        min_win_rate=min_win_rate,
        min_maker_quote_count=min_maker_quote_count,
        min_maker_mean_markout_cents=min_maker_mean_markout_cents,
    )
    cooldown_rows = [
        row
        for rows in (report.get("groups") or {}).values()
        for row in rows
        if isinstance(row, dict)
        and row.get("action") == "cooldown_exploration"
    ]
    candidate_rules: List[Dict[str, Any]] = []
    skipped_groups: Dict[str, int] = {}
    for row in cooldown_rows:
        group = str(row.get("group_name") or "").strip()
        if group not in allowed:
            skipped_groups[group or "unknown"] = skipped_groups.get(group or "unknown", 0) + 1
            continue
        rule = _quarantine_surface_rule_from_group(row)
        if not rule.get("dimensions"):
            skipped_groups[group or "unknown"] = skipped_groups.get(group or "unknown", 0) + 1
            continue
        candidate_rules.append(rule)
    rules, unsupported_rules = _split_rules_by_supported_dimensions(candidate_rules)
    return {
        "schema_version": RISK_RULE_SCHEMA_VERSION,
        "source": "quarantine_surface_cooldown",
        "paper_journal_dir": str(paper_journal_dir),
        "quarantine_journal_dir": str(quarantine_journal_dir or DEFAULT_QUARANTINE_JOURNAL_DIR),
        "thresholds": {
            "min_decision_count": max(1, int(min_decision_count)),
            "min_promote_count": max(1, int(min_promote_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
            "min_maker_quote_count": max(0, int(min_maker_quote_count)),
            "min_maker_mean_markout_cents": float(min_maker_mean_markout_cents),
        },
        "allowed_groups": sorted(allowed),
        "surface_hard_conclusion": report.get("hard_conclusion"),
        "surface_evidence": report.get("evidence") or {},
        "surface_action_counts": report.get("action_counts") or [],
        "raw_cooldown_group_count": len(cooldown_rows),
        "skipped_group_counts": [
            {"group": group, "count": count}
            for group, count in sorted(skipped_groups.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "raw_rule_count": len(candidate_rules),
        "rule_count": len(rules),
        "unsupported_rule_count": len(unsupported_rules),
        "unsupported_rule_groups": sorted(
            {
                str(rule.get("group") or "unknown")
                for rule in unsupported_rules
            }
        ),
        "unsupported_rules_sample": unsupported_rules[:10],
        "rules": rules,
    }


def dump_risk_rules(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
