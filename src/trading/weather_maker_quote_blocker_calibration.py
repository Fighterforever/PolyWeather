from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_maker_quote_journal import summarize_maker_quote_strata
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR, _safe_float, stable_json_hash, utc_now_iso


MAKER_QUOTE_BLOCKER_CALIBRATION_SCHEMA_VERSION = "polyweather_weather_maker_quote_blocker_calibration.v1"


def _parse_negative_rule_reason(reason: str) -> Optional[Dict[str, Any]]:
    text = str(reason or "").strip()
    prefix = "negative_markout_rule:"
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix) :]
    if ":" not in rest:
        return None
    group, dimension_text = rest.split(":", 1)
    group = group.strip()
    if not group.startswith("maker_quote_"):
        return None
    dimensions: Dict[str, str] = {}
    for part in dimension_text.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip().lower()
        if key and value:
            dimensions[key] = value
    if not dimensions:
        return None
    return {
        "reason": text,
        "group": group,
        "summary_group": group.removeprefix("maker_quote_"),
        "dimensions": dimensions,
        "specificity": _maker_quote_rule_specificity(group),
    }


def _maker_quote_rule_specificity(group: str) -> str:
    if "_and_" in group:
        return "specific"
    if group in {
        "maker_quote_by_market_family",
        "maker_quote_by_quarantine_reason",
        "maker_quote_by_quarantine_blocker_scope",
        "maker_quote_by_quote_strategy",
    }:
        return "broad"
    return "medium"


def _row_matches_dimensions(row: Dict[str, Any], dimensions: Dict[str, str]) -> bool:
    return all(str(row.get(key) or "").strip().lower() == value for key, value in dimensions.items())


def _find_evidence_row(strata: Dict[str, Any], parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rows = strata.get(str(parsed.get("summary_group") or ""))
    if not isinstance(rows, list):
        return None
    dimensions = parsed.get("dimensions") if isinstance(parsed.get("dimensions"), dict) else {}
    for row in rows:
        if isinstance(row, dict) and _row_matches_dimensions(row, dimensions):
            return row
    return None


def _iter_current_rows(signal_report: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for section in ("candidates", "watch", "quarantine"):
        rows = signal_report.get(section) if isinstance(signal_report, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield row


def _action_for_evidence(
    evidence: Optional[Dict[str, Any]],
    *,
    min_count: int,
    min_inferred_fills: int,
    min_fill_inference_rate: float,
    min_mean_maker_markout_cents: float,
    min_maker_win_rate: float,
) -> Tuple[str, List[str]]:
    if not evidence:
        return "collect_more_maker_quote_evidence", ["missing_maker_quote_stratum"]
    count = int(evidence.get("count") or 0)
    inferred_count = int(evidence.get("inferred_fill_count") or 0)
    fill_rate = _safe_float(evidence.get("fill_inference_rate"))
    mean_maker = _safe_float(evidence.get("mean_maker_markout_cents"))
    win_rate = _safe_float(evidence.get("maker_markout_win_rate"))
    failures: List[str] = []
    if count < max(1, int(min_count)):
        failures.append(f"insufficient_quote_count_{count}_of_{max(1, int(min_count))}")
    if inferred_count < max(1, int(min_inferred_fills)):
        failures.append(f"insufficient_inferred_fills_{inferred_count}_of_{max(1, int(min_inferred_fills))}")
    if fill_rate is None:
        failures.append("fill_inference_rate_missing")
    elif fill_rate < float(min_fill_inference_rate):
        failures.append("fill_inference_rate_below_threshold")
    if mean_maker is None:
        failures.append("mean_maker_markout_missing")
    elif mean_maker < float(min_mean_maker_markout_cents):
        failures.append("mean_maker_markout_below_threshold")
    if win_rate is None:
        failures.append("maker_win_rate_missing")
    elif win_rate < float(min_maker_win_rate):
        failures.append("maker_win_rate_below_threshold")

    if not failures:
        return "eligible_for_maker_focus_review", []
    if count >= max(1, int(min_count)) and (
        inferred_count == 0
        or (mean_maker is not None and mean_maker < float(min_mean_maker_markout_cents))
        or (win_rate is not None and win_rate < float(min_maker_win_rate))
    ):
        return "keep_blocked_by_maker_quote_evidence", failures
    return "collect_more_maker_quote_evidence", failures


def _compact_evidence(evidence: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not evidence:
        return None
    keys = (
        "count",
        "inferred_fill_count",
        "fill_inference_rate",
        "maker_markout_win_count",
        "maker_markout_win_rate",
        "mean_maker_markout_cents",
        "mean_missed_taker_markout_cents",
    )
    return {key: evidence.get(key) for key in keys if key in evidence}


def build_maker_quote_blocker_calibration_report(
    signal_report: Dict[str, Any],
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    latest_only: bool = True,
    min_count: int = 3,
    min_inferred_fills: int = 3,
    min_fill_inference_rate: float = 0.05,
    min_mean_maker_markout_cents: float = 0.0,
    min_maker_win_rate: float = 0.55,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    strata = summarize_maker_quote_strata(journal_dir, latest_only=latest_only, min_count=1)
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in _iter_current_rows(signal_report):
        maker_hits = [
            parsed
            for parsed in (
                _parse_negative_rule_reason(str(reason))
                for reason in item.get("risk_rule_hits") or []
            )
            if parsed is not None
        ]
        if not maker_hits:
            continue
        would_be = str(item.get("would_be_decision_without_risk_rules") or item.get("decision") or "").strip()
        for parsed in maker_hits:
            reason = str(parsed["reason"])
            row = grouped.setdefault(
                reason,
                {
                    **parsed,
                    "current_hit_count": 0,
                    "current_candidate_like_count": 0,
                    "current_samples": [],
                },
            )
            row["current_hit_count"] += 1
            if would_be == "candidate":
                row["current_candidate_like_count"] += 1
            samples = row["current_samples"]
            if len(samples) < 5:
                samples.append(
                    {
                        "market_slug": item.get("market_slug"),
                        "city": item.get("city"),
                        "side": item.get("side"),
                        "bucket_type": item.get("bucket_type"),
                        "price": item.get("price"),
                        "spread": item.get("spread"),
                        "edge_percent": item.get("edge_percent"),
                        "would_be_decision_without_risk_rules": item.get("would_be_decision_without_risk_rules"),
                    }
                )

    blockers: List[Dict[str, Any]] = []
    action_counts: Dict[str, int] = {}
    specificity_counts: Dict[str, int] = {}
    for row in grouped.values():
        evidence = _find_evidence_row(strata, row)
        action, failures = _action_for_evidence(
            evidence,
            min_count=min_count,
            min_inferred_fills=min_inferred_fills,
            min_fill_inference_rate=min_fill_inference_rate,
            min_mean_maker_markout_cents=min_mean_maker_markout_cents,
            min_maker_win_rate=min_maker_win_rate,
        )
        action_counts[action] = action_counts.get(action, 0) + 1
        specificity = str(row.get("specificity") or "unknown")
        specificity_counts[specificity] = specificity_counts.get(specificity, 0) + 1
        blockers.append(
            {
                "reason": row.get("reason"),
                "group": row.get("group"),
                "summary_group": row.get("summary_group"),
                "specificity": specificity,
                "dimensions": row.get("dimensions") or {},
                "current_hit_count": row.get("current_hit_count"),
                "current_candidate_like_count": row.get("current_candidate_like_count"),
                "action": action,
                "failure_reasons": failures,
                "evidence": _compact_evidence(evidence),
                "current_samples": row.get("current_samples") or [],
            }
        )
    blockers = sorted(
        blockers,
        key=lambda row: (
            0 if row.get("action") == "eligible_for_maker_focus_review" else 1,
            str(row.get("specificity") or ""),
            -int(row.get("current_candidate_like_count") or 0),
            -int(row.get("current_hit_count") or 0),
            str(row.get("reason") or ""),
        ),
    )
    eligible_count = action_counts.get("eligible_for_maker_focus_review", 0)
    keep_blocked_count = action_counts.get("keep_blocked_by_maker_quote_evidence", 0)
    collect_more_count = action_counts.get("collect_more_maker_quote_evidence", 0)
    specific_candidate_blocker_count = len(
        [
            row
            for row in blockers
            if row.get("specificity") == "specific" and int(row.get("current_candidate_like_count") or 0) > 0
        ]
    )
    identity = {
        "source_snapshot_id": signal_report.get("source_snapshot_id") if isinstance(signal_report, dict) else None,
        "journal_dir": str(Path(journal_dir)),
        "blockers": [
            {
                "reason": row.get("reason"),
                "action": row.get("action"),
                "current_hit_count": row.get("current_hit_count"),
            }
            for row in blockers
        ],
    }
    hard_conclusion = (
        "maker_quote_blockers_have_promotable_strata"
        if eligible_count > 0
        else (
            "maker_quote_specific_blockers_currently_negative"
            if specific_candidate_blocker_count > 0 and keep_blocked_count > 0
            else (
                "maker_quote_blockers_need_more_evidence"
                if collect_more_count > 0
                else "maker_quote_no_current_blockers"
            )
        )
    )
    return {
        "schema_version": MAKER_QUOTE_BLOCKER_CALIBRATION_SCHEMA_VERSION,
        "report_type": "maker_quote_blocker_calibration",
        "generated_at": generated_at,
        "calibration_id": stable_json_hash(identity, length=20),
        "journal_dir": str(Path(journal_dir)),
        "latest_only": bool(latest_only),
        "paper_only": True,
        "counts_for_live_gate": False,
        "thresholds": {
            "min_count": max(1, int(min_count)),
            "min_inferred_fills": max(1, int(min_inferred_fills)),
            "min_fill_inference_rate": float(min_fill_inference_rate),
            "min_mean_maker_markout_cents": float(min_mean_maker_markout_cents),
            "min_maker_win_rate": float(min_maker_win_rate),
        },
        "source_snapshot_id": signal_report.get("source_snapshot_id") if isinstance(signal_report, dict) else None,
        "strata_summary": {
            "quote_markout_count": strata.get("quote_markout_count"),
            "inferred_fill_count": strata.get("inferred_fill_count"),
            "fill_inference_rate": strata.get("fill_inference_rate"),
            "mean_maker_markout_cents": strata.get("mean_maker_markout_cents"),
            "maker_markout_win_rate": strata.get("maker_markout_win_rate"),
        },
        "blocker_count": len(blockers),
        "specific_candidate_blocker_count": specific_candidate_blocker_count,
        "action_counts": [
            {"action": action, "count": count}
            for action, count in sorted(action_counts.items(), key=lambda item: item[0])
        ],
        "specificity_counts": [
            {"specificity": specificity, "count": count}
            for specificity, count in sorted(specificity_counts.items(), key=lambda item: item[0])
        ],
        "eligible_blocker_count": eligible_count,
        "keep_blocked_count": keep_blocked_count,
        "collect_more_count": collect_more_count,
        "blockers": blockers,
        "hard_conclusion": hard_conclusion,
    }


def dump_maker_quote_blocker_calibration_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
