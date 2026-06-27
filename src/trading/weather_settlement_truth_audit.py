from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements
from src.trading.weather_paper_journal import _safe_float


SETTLEMENT_TRUTH_AUDIT_SCHEMA_VERSION = "polyweather_weather_settlement_truth_audit.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        value = _text(record.get(field)) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _rounded_settlement_value(value: float, rounding: str) -> float:
    normalized = _text(rounding).lower()
    if normalized in {"integer_nearest", "nearest_integer", "round"}:
        return float(math.floor(float(value) + 0.5))
    return float(value)


def _yes_payout(record: Dict[str, Any]) -> Optional[float]:
    settled = record.get("settled_probability_by_outcome")
    if not isinstance(settled, dict):
        return None
    return _safe_float(settled.get("Yes") if "Yes" in settled else settled.get("yes"))


def _bucket_type(record: Dict[str, Any]) -> str:
    parsed = record.get("parsed_temperature_spec") if isinstance(record.get("parsed_temperature_spec"), dict) else {}
    value = _text(parsed.get("comparator")).lower()
    if value:
        return value
    label = _text(record.get("bucket_label"))
    if label.startswith(">="):
        return "ge"
    if label.startswith("<="):
        return "le"
    if label.startswith("="):
        return "eq"
    if "-" in label:
        return "range"
    return "unknown"


def expected_yes_from_official_final_value(record: Dict[str, Any]) -> Dict[str, Any]:
    parsed = record.get("parsed_temperature_spec") if isinstance(record.get("parsed_temperature_spec"), dict) else None
    if not parsed:
        return {"status": "unsupported_non_temperature_market"}
    official_value = _safe_float(record.get("official_final_value"))
    if official_value is None:
        return {"status": "missing_official_final_value"}

    bucket_type = _bucket_type(record)
    threshold = _safe_float(parsed.get("threshold"))
    upper_threshold = _safe_float(parsed.get("upper_threshold"))
    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    rounded_value = _rounded_settlement_value(
        float(official_value),
        _text(spec.get("rounding")) or "integer_nearest",
    )
    if threshold is None:
        return {"status": "missing_threshold", "official_final_value": official_value}

    expected: Optional[bool]
    if bucket_type == "ge":
        expected = rounded_value >= float(threshold)
    elif bucket_type == "le":
        expected = rounded_value <= float(threshold)
    elif bucket_type == "eq":
        expected = rounded_value == float(threshold)
    elif bucket_type == "range":
        if upper_threshold is None:
            return {
                "status": "missing_upper_threshold",
                "official_final_value": official_value,
                "rounded_official_final_value": rounded_value,
            }
        expected = float(threshold) <= rounded_value <= float(upper_threshold)
    else:
        return {
            "status": "unsupported_bucket_type",
            "bucket_type": bucket_type,
            "official_final_value": official_value,
            "rounded_official_final_value": rounded_value,
        }

    return {
        "status": "ready",
        "expected_yes": bool(expected),
        "expected_yes_payout": 1.0 if expected else 0.0,
        "official_final_value": official_value,
        "rounded_official_final_value": rounded_value,
        "bucket_type": bucket_type,
        "threshold": float(threshold),
        "upper_threshold": upper_threshold,
    }


def audit_settlement_truth_record(record: Dict[str, Any]) -> Dict[str, Any]:
    expectation = expected_yes_from_official_final_value(record)
    yes_payout = _yes_payout(record)
    status = str(expectation.get("status") or "unknown")
    match: Optional[bool] = None
    payout_gap: Optional[float] = None
    if status == "ready":
        if yes_payout is None:
            status = "missing_yes_payout"
        else:
            expected_payout = float(expectation["expected_yes_payout"])
            payout_gap = round(float(yes_payout) - expected_payout, 8)
            match = abs(payout_gap) <= 1e-6
            status = "pass" if match else "mismatch"

    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    return {
        "schema_version": SETTLEMENT_TRUTH_AUDIT_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "diagnostic_only": True,
        "status": status,
        "match": match,
        "market_id": record.get("market_id"),
        "market_slug": record.get("market_slug"),
        "city": record.get("city") or (record.get("parsed_temperature_spec") or {}).get("city"),
        "target_date": record.get("target_date") or (record.get("parsed_temperature_spec") or {}).get("target_date"),
        "bucket_label": record.get("bucket_label"),
        "bucket_type": expectation.get("bucket_type") or _bucket_type(record),
        "station_code": spec.get("station_code"),
        "settlement_source": spec.get("settlement_source"),
        "official_final_value": expectation.get("official_final_value"),
        "rounded_official_final_value": expectation.get("rounded_official_final_value"),
        "official_final_value_source": record.get("official_final_value_source"),
        "expected_yes": expectation.get("expected_yes"),
        "expected_yes_payout": expectation.get("expected_yes_payout"),
        "settled_yes_payout": yes_payout,
        "payout_gap": payout_gap,
        "threshold": expectation.get("threshold"),
        "upper_threshold": expectation.get("upper_threshold"),
        "gap_reason": status if status != "pass" else None,
    }


def build_settlement_truth_audit_report(
    records: Iterable[Dict[str, Any]],
    *,
    max_mismatch_samples: int = 10,
    max_gap_samples: int = 10,
) -> Dict[str, Any]:
    audits = [
        audit_settlement_truth_record(record)
        for record in records
        if isinstance(record, dict) and record.get("status") == "resolved"
    ]
    passed = [row for row in audits if row.get("status") == "pass"]
    mismatches = [row for row in audits if row.get("status") == "mismatch"]
    gaps = [row for row in audits if row.get("status") not in {"pass", "mismatch"}]
    audited_with_official = [
        row
        for row in audits
        if row.get("official_final_value") is not None and row.get("settled_yes_payout") is not None
    ]
    if not audits:
        hard_conclusion = "settlement_truth_audit_no_records"
    elif mismatches:
        hard_conclusion = "settlement_truth_audit_mismatch"
    elif gaps:
        hard_conclusion = "settlement_truth_audit_has_gaps"
    else:
        hard_conclusion = "settlement_truth_audit_pass"
    return {
        "schema_version": SETTLEMENT_TRUTH_AUDIT_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "diagnostic_only": True,
        "hard_conclusion": hard_conclusion,
        "record_count": len(audits),
        "audited_with_official_count": len(audited_with_official),
        "pass_count": len(passed),
        "mismatch_count": len(mismatches),
        "gap_count": len(gaps),
        "by_status": _count_by(audits, "status", key_name="status"),
        "by_settlement_source": _count_by(audited_with_official, "settlement_source", key_name="settlement_source"),
        "by_city": _count_by(audited_with_official, "city", key_name="city"),
        "by_bucket_type": _count_by(audited_with_official, "bucket_type", key_name="bucket_type"),
        "mismatch_samples": mismatches[: max(0, int(max_mismatch_samples))],
        "gap_samples": gaps[: max(0, int(max_gap_samples))],
        "audits": audits,
    }


def build_settlement_truth_audit_report_from_dir(
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    *,
    official_value_supplements_path: Optional[str | Path] = None,
    max_mismatch_samples: int = 10,
    max_gap_samples: int = 10,
) -> Dict[str, Any]:
    return build_settlement_truth_audit_report(
        load_closed_backfill_records_with_snapshot_supplements(
            backfill_dir,
            official_value_supplements_path=official_value_supplements_path,
        ),
        max_mismatch_samples=max_mismatch_samples,
        max_gap_samples=max_gap_samples,
    )
