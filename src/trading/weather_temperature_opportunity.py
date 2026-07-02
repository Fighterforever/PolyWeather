from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_paper_journal import _safe_float, stable_json_hash, utc_now_iso


TEMPERATURE_OPPORTUNITY_SCHEMA_VERSION = "polyweather_weather_temperature_opportunity.v1"


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _horizon_hours(row: Dict[str, Any], generated_at_dt: datetime) -> Optional[float]:
    end_dt = _parse_utc_datetime(row.get("end_date"))
    if end_dt is None:
        return None
    return round((end_dt - generated_at_dt).total_seconds() / 3600.0, 6)


def _as_reason_set(values: Iterable[Any]) -> List[str]:
    return sorted({str(value) for value in values or [] if str(value)})


def _row_key(row: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    return (
        row.get("market_slug"),
        row.get("side"),
        row.get("bucket_label") or row.get("outcome"),
        row.get("bucket_type"),
    )


def _non_risk_blockers(row: Dict[str, Any]) -> List[str]:
    explicit = row.get("non_risk_blockers")
    if isinstance(explicit, list):
        return _as_reason_set(explicit)
    risk_hits = set(_as_reason_set(row.get("risk_rule_hits") or []))
    return [
        reason
        for reason in _as_reason_set(row.get("blockers") or [])
        if reason not in risk_hits
    ]


def _iter_current_rows(signal_report: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    seen: set[Tuple[Any, Any, Any]] = set()
    for section in ("candidates", "watch", "quarantine"):
        rows = signal_report.get(section) if isinstance(signal_report, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            yield row
    gap_report = signal_report.get("candidate_gap_report") if isinstance(signal_report, dict) else {}
    near_candidates = gap_report.get("near_candidates") if isinstance(gap_report, dict) else []
    if not isinstance(near_candidates, list):
        return
    for row in near_candidates:
        if not isinstance(row, dict):
            continue
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        yield row


def _maker_evidence_by_reason(report: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    evidence: Dict[str, Dict[str, Any]] = {}
    for row in report.get("blockers") or report.get("top_blockers") or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "").strip()
        if reason:
            evidence[reason] = row
    return evidence


def _maker_hit_state(
    reasons: List[str],
    maker_evidence: Dict[str, Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    maker_hits = [
        reason
        for reason in reasons
        if reason.startswith("negative_markout_rule:maker_quote_")
    ]
    if not maker_hits:
        return "none", []
    rows: List[Dict[str, Any]] = []
    states: List[str] = []
    for reason in maker_hits:
        evidence_row = maker_evidence.get(reason) or {}
        evidence = evidence_row.get("evidence") if isinstance(evidence_row.get("evidence"), dict) else {}
        action = str(evidence_row.get("action") or "").strip()
        mean_maker = _safe_float(evidence.get("mean_maker_markout_cents"))
        win_rate = _safe_float(evidence.get("maker_markout_win_rate"))
        inferred_count = int(evidence.get("inferred_fill_count") or 0)
        if action == "eligible_for_maker_focus_review":
            state = "eligible"
        elif mean_maker is not None and mean_maker < 0:
            state = "negative"
        elif win_rate is not None and win_rate < 0.55:
            state = "negative"
        elif action == "keep_blocked_by_maker_quote_evidence" and inferred_count > 0:
            state = "negative"
        else:
            state = "insufficient_evidence"
        states.append(state)
        rows.append(
            {
                "reason": reason,
                "state": state,
                "action": action or None,
                "evidence": evidence,
                "failure_reasons": evidence_row.get("failure_reasons") or [],
            }
        )
    if "negative" in states:
        return "negative", rows
    if "insufficient_evidence" in states:
        return "insufficient_evidence", rows
    if "eligible" in states:
        return "eligible", rows
    return "unknown", rows


def _opportunity_row(
    row: Dict[str, Any],
    *,
    category: str,
    maker_state: str,
    maker_details: List[Dict[str, Any]],
    non_risk: List[str],
    horizon_hours: Optional[float],
) -> Dict[str, Any]:
    return {
        "category": category,
        "market_slug": row.get("market_slug"),
        "market_id": row.get("market_id"),
        "token_id": row.get("token_id"),
        "question": row.get("question"),
        "city": row.get("city"),
        "side": row.get("side"),
        "outcome": row.get("outcome"),
        "bucket_label": row.get("bucket_label"),
        "bucket_type": row.get("bucket_type"),
        "price": row.get("price"),
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "spread": row.get("spread"),
        "liquidity": row.get("liquidity"),
        "bid_depth_usdc_3c": row.get("bid_depth_usdc_3c"),
        "ask_depth_usdc_3c": row.get("ask_depth_usdc_3c"),
        "edge_percent": row.get("edge_percent"),
        "model_probability": row.get("model_probability"),
        "market_probability": row.get("market_probability"),
        "score": row.get("score"),
        "decision": row.get("decision"),
        "would_be_decision_without_risk_rules": row.get("would_be_decision_without_risk_rules"),
        "non_risk_blockers": non_risk,
        "risk_rule_hits": _as_reason_set(row.get("risk_rule_hits") or []),
        "maker_hit_state": maker_state,
        "maker_hit_details": maker_details,
        "end_date": row.get("end_date"),
        "horizon_hours": horizon_hours,
    }


def build_temperature_opportunity_report(
    signal_report: Dict[str, Any],
    *,
    maker_quote_blocker_calibration_report: Optional[Dict[str, Any]] = None,
    excluded_bucket_types: Iterable[str] = ("eq",),
    max_horizon_hours: float = 48.0,
    max_items: int = 20,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify current temperature rows without changing trade decisions."""

    generated = generated_at or utc_now_iso()
    generated_at_dt = _parse_utc_datetime(generated) or datetime.now(timezone.utc).replace(microsecond=0)
    excluded = {str(value).strip().lower() for value in excluded_bucket_types if str(value).strip()}
    maker_evidence = _maker_evidence_by_reason(maker_quote_blocker_calibration_report)

    categories: Dict[str, List[Dict[str, Any]]] = {
        "eligible_for_formal_paper": [],
        "blocked_by_negative_maker_evidence": [],
        "collect_more_maker_evidence": [],
        "blocked_by_market_surface": [],
        "blocked_by_non_maker_risk": [],
    }
    scanned_temperature_count = 0
    excluded_bucket_count = 0
    outside_horizon_count = 0

    for row in _iter_current_rows(signal_report):
        if str(row.get("market_family") or "").strip().lower() != "temperature":
            continue
        scanned_temperature_count += 1
        bucket_type = str(row.get("bucket_type") or "").strip().lower()
        if bucket_type in excluded:
            excluded_bucket_count += 1
            continue
        horizon = _horizon_hours(row, generated_at_dt)
        if horizon is not None and (horizon <= 0 or horizon > float(max_horizon_hours)):
            outside_horizon_count += 1
            continue

        risk_hits = _as_reason_set(row.get("risk_rule_hits") or [])
        maker_state, maker_details = _maker_hit_state(risk_hits, maker_evidence)
        non_risk = _non_risk_blockers(row)
        non_maker_risk_hits = [
            reason
            for reason in risk_hits
            if not reason.startswith("negative_markout_rule:maker_quote_")
        ]

        if non_risk:
            category = "blocked_by_market_surface"
        elif maker_state == "negative":
            category = "blocked_by_negative_maker_evidence"
        elif maker_state == "insufficient_evidence":
            category = "collect_more_maker_evidence"
        elif non_maker_risk_hits:
            category = "blocked_by_non_maker_risk"
        else:
            category = "eligible_for_formal_paper"

        categories[category].append(
            _opportunity_row(
                row,
                category=category,
                maker_state=maker_state,
                maker_details=maker_details,
                non_risk=non_risk,
                horizon_hours=horizon,
            )
        )

    for rows in categories.values():
        rows.sort(
            key=lambda item: (
                -float(item.get("edge_percent") or -999.0),
                float(item.get("spread") or 999.0),
                -float(item.get("liquidity") or 0.0),
                str(item.get("market_slug") or ""),
            )
        )

    counts = {key: len(value) for key, value in categories.items()}
    hard_conclusion = (
        "temperature_opportunity_has_formal_paper_candidates"
        if counts["eligible_for_formal_paper"] > 0
        else (
            "temperature_opportunity_blocked_by_negative_maker"
            if counts["blocked_by_negative_maker_evidence"] > 0
            else (
                "temperature_opportunity_needs_maker_evidence"
                if counts["collect_more_maker_evidence"] > 0
                else "temperature_opportunity_no_non_eq_surface"
            )
        )
    )
    identity = {
        "source_snapshot_id": signal_report.get("source_snapshot_id") if isinstance(signal_report, dict) else None,
        "counts": counts,
        "hard_conclusion": hard_conclusion,
    }
    return {
        "schema_version": TEMPERATURE_OPPORTUNITY_SCHEMA_VERSION,
        "generated_at": generated,
        "opportunity_id": stable_json_hash(identity, length=20),
        "source_snapshot_id": signal_report.get("source_snapshot_id") if isinstance(signal_report, dict) else None,
        "paper_only": True,
        "counts_for_live_gate": False,
        "config": {
            "excluded_bucket_types": sorted(excluded),
            "max_horizon_hours": float(max_horizon_hours),
            "max_items": max(1, int(max_items)),
        },
        "scanned_temperature_count": scanned_temperature_count,
        "excluded_bucket_count": excluded_bucket_count,
        "outside_horizon_count": outside_horizon_count,
        "category_counts": [
            {"category": category, "count": count}
            for category, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "eligible_for_formal_paper_count": counts["eligible_for_formal_paper"],
        "blocked_by_negative_maker_count": counts["blocked_by_negative_maker_evidence"],
        "collect_more_maker_evidence_count": counts["collect_more_maker_evidence"],
        "blocked_by_market_surface_count": counts["blocked_by_market_surface"],
        "blocked_by_non_maker_risk_count": counts["blocked_by_non_maker_risk"],
        "eligible_for_formal_paper": categories["eligible_for_formal_paper"][: max(1, int(max_items))],
        "blocked_by_negative_maker_evidence": categories["blocked_by_negative_maker_evidence"][: max(1, int(max_items))],
        "collect_more_maker_evidence": categories["collect_more_maker_evidence"][: max(1, int(max_items))],
        "blocked_by_market_surface": categories["blocked_by_market_surface"][: max(1, int(max_items))],
        "blocked_by_non_maker_risk": categories["blocked_by_non_maker_risk"][: max(1, int(max_items))],
        "hard_conclusion": hard_conclusion,
    }
