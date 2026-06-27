from __future__ import annotations

import math
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements
from src.trading.weather_paper_journal import _safe_float, load_jsonl
from src.trading.weather_settlement_truth_audit import audit_settlement_truth_record


SETTLEMENT_CALIBRATION_SCHEMA_VERSION = "polyweather_weather_settlement_calibration.v1"

PROBABILITY_FIELDS = (
    "p_model",
    "model_probability",
    "market_implied_de_vig_yes_probability",
    "market_implied_yes_price",
    "probability",
)

ENTRY_PRICE_FIELDS = (
    "historical_entry_price",
    "entry_price",
    "paper_entry_price",
)

EVIDENCE_TIME_FIELDS = (
    "available_at",
    "generated_at",
    "recorded_at",
    "snapshot_at",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


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


def _first_text(row: Dict[str, Any], fields: Iterable[str]) -> Optional[str]:
    for field in fields:
        value = _text(row.get(field))
        if value:
            return value
    return None


def _mean(values: Iterable[float]) -> Optional[float]:
    items = [float(value) for value in values]
    if not items:
        return None
    return sum(items) / len(items)


def _rounded(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _probability_prediction(record: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    for field in PROBABILITY_FIELDS:
        value = _safe_float(record.get(field))
        if value is not None:
            return _clamp_probability(value), field
    return None, None


def _entry_price(record: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    for field in ENTRY_PRICE_FIELDS:
        value = _safe_float(record.get(field))
        if value is not None:
            return value, field
    return None, None


def _record_cutoff_time(record: Dict[str, Any]) -> Optional[datetime]:
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


def _evidence_time(row: Dict[str, Any]) -> Optional[datetime]:
    for field in EVIDENCE_TIME_FIELDS:
        parsed = _parse_utc(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _evidence_time_text(row: Dict[str, Any]) -> Optional[str]:
    return _first_text(row, EVIDENCE_TIME_FIELDS)


def _is_yes_evidence(row: Dict[str, Any]) -> bool:
    side = _text(row.get("side") or row.get("outcome")).lower()
    return side in {"", "yes", "y", "buy_yes"}


def _market_identity_keys(row: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    token_id = _text(row.get("token_id"))
    if token_id:
        keys.append(f"token:{token_id}")
    market_slug = _text(row.get("market_slug"))
    if market_slug:
        keys.append(f"slug:{market_slug}:yes")
        keys.append(f"slug:{market_slug}:*")
    market_id = _text(row.get("market_id"))
    if market_id:
        keys.append(f"id:{market_id}:yes")
        keys.append(f"id:{market_id}:*")
    return keys


def _record_evidence_keys(record: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    token_map = record.get("token_id_by_outcome") if isinstance(record.get("token_id_by_outcome"), dict) else {}
    yes_token_id = _text(token_map.get("Yes") or token_map.get("yes"))
    if yes_token_id:
        keys.append(f"token:{yes_token_id}")
    market_slug = _text(record.get("market_slug"))
    if market_slug:
        keys.append(f"slug:{market_slug}:yes")
        keys.append(f"slug:{market_slug}:*")
    market_id = _text(record.get("market_id"))
    if market_id:
        keys.append(f"id:{market_id}:yes")
        keys.append(f"id:{market_id}:*")
    return keys


def _evidence_sort_key(row: Dict[str, Any]) -> Tuple[datetime, str]:
    return (_evidence_time(row) or datetime.min.replace(tzinfo=timezone.utc), _text(row.get("source")))


def load_historical_evidence_supplements(path: str | Path) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    if file_path.suffix.lower() == ".jsonl":
        return load_jsonl(file_path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("evidence") or payload.get("supplements")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return [payload]
    return []


def apply_historical_evidence_supplements(
    records: Iterable[Dict[str, Any]],
    *,
    supplements: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    evidence_rows = [row for row in supplements if isinstance(row, dict)]
    index: Dict[str, List[Dict[str, Any]]] = {}
    skipped_non_yes = 0
    skipped_no_probability_or_price = 0
    for row in evidence_rows:
        if not _is_yes_evidence(row):
            skipped_non_yes += 1
            continue
        probability, _probability_source = _probability_prediction(row)
        entry_price, _entry_source = _entry_price(row)
        if probability is None and entry_price is None:
            skipped_no_probability_or_price += 1
            continue
        for key in _market_identity_keys(row):
            index.setdefault(key, []).append(row)
    for rows in index.values():
        rows.sort(key=_evidence_sort_key, reverse=True)

    supplemented: List[Dict[str, Any]] = []
    applied_count = 0
    missing_match_count = 0
    missing_cutoff_count = 0
    missing_time_count = 0
    future_evidence_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        cutoff = _record_cutoff_time(row)
        if cutoff is None:
            missing_cutoff_count += 1
            row["historical_evidence_gap_reason"] = "missing_market_end_time"
            supplemented.append(row)
            continue
        candidates: List[Dict[str, Any]] = []
        seen_ids = set()
        for key in _record_evidence_keys(row):
            for evidence in index.get(key, []):
                identity = id(evidence)
                if identity in seen_ids:
                    continue
                seen_ids.add(identity)
                candidates.append(evidence)
        if not candidates:
            missing_match_count += 1
            row["historical_evidence_gap_reason"] = "missing_matching_historical_evidence"
            supplemented.append(row)
            continue
        visible: List[Dict[str, Any]] = []
        saw_missing_time = False
        saw_future = False
        for evidence in candidates:
            available_at = _evidence_time(evidence)
            if available_at is None:
                saw_missing_time = True
                continue
            if available_at > cutoff:
                saw_future = True
                continue
            visible.append(evidence)
        if not visible:
            if saw_missing_time:
                missing_time_count += 1
                row["historical_evidence_gap_reason"] = "missing_historical_evidence_available_at"
            elif saw_future:
                future_evidence_count += 1
                row["historical_evidence_gap_reason"] = "historical_evidence_after_market_end_time"
            else:
                row["historical_evidence_gap_reason"] = "missing_visible_historical_evidence"
            supplemented.append(row)
            continue

        best = sorted(visible, key=_evidence_sort_key, reverse=True)[0]
        probability, probability_source = _probability_prediction(best)
        entry_price, entry_source = _entry_price(best)
        if probability is not None and _probability_prediction(row)[0] is None:
            row["model_probability"] = float(probability)
            row["historical_prediction_probability_source"] = probability_source
        if entry_price is not None and _entry_price(row)[0] is None:
            row["historical_entry_price"] = float(entry_price)
            row["historical_entry_price_source"] = entry_source
        row["historical_evidence_supplement_applied"] = True
        row["historical_evidence_source"] = best.get("source") or best.get("evidence_source")
        row["historical_evidence_available_at"] = _evidence_time_text(best)
        row["historical_evidence_orderbook_snapshot_id"] = best.get("orderbook_snapshot_id") or best.get("snapshot_id")
        row["historical_evidence_no_lookahead"] = True
        row.pop("historical_evidence_gap_reason", None)
        applied_count += 1
        supplemented.append(row)

    return supplemented, {
        "input_record_count": len(supplemented),
        "supplement_row_count": len(evidence_rows),
        "index_key_count": len(index),
        "applied_record_count": applied_count,
        "missing_match_count": missing_match_count,
        "missing_cutoff_count": missing_cutoff_count,
        "missing_time_count": missing_time_count,
        "future_evidence_count": future_evidence_count,
        "skipped_non_yes_count": skipped_non_yes,
        "skipped_no_probability_or_price_count": skipped_no_probability_or_price,
    }


def _brier(probability: float, outcome: bool) -> float:
    y = 1.0 if outcome else 0.0
    return (float(probability) - y) ** 2


def _log_loss(probability: float, outcome: bool) -> float:
    p = min(1.0 - 1e-9, max(1e-9, float(probability)))
    return -math.log(p if outcome else 1.0 - p)


def _group_keys(row: Dict[str, Any]) -> List[Tuple[str, str]]:
    source = _text(row.get("settlement_source")) or "unknown"
    city = _text(row.get("city")).lower() or "unknown"
    station = _text(row.get("station_code")).upper() or "unknown"
    bucket_type = _text(row.get("bucket_type")).lower() or "unknown"
    return [
        ("global", "all"),
        ("settlement_source", source),
        ("city", city),
        ("station", station),
        ("bucket_type", bucket_type),
        ("source_bucket_type", f"{source}|{bucket_type}"),
        ("city_bucket_type", f"{city}|{bucket_type}"),
        ("station_bucket_type", f"{station}|{bucket_type}"),
    ]


def _base_stats() -> Dict[str, Any]:
    return {
        "sample_count": 0,
        "yes_count": 0,
        "official_values": [],
        "rounded_official_values": [],
        "thresholds": [],
        "probability_predictions": [],
        "brier_values": [],
        "log_loss_values": [],
        "resolved_pnls": [],
        "prediction_sources": {},
        "entry_price_sources": {},
    }


def _add_count(mapping: Dict[str, int], key: Optional[str]) -> None:
    value = _text(key) or "unknown"
    mapping[value] = mapping.get(value, 0) + 1


def _update_stats(stats: Dict[str, Any], row: Dict[str, Any]) -> None:
    stats["sample_count"] += 1
    if row.get("expected_yes") is True:
        stats["yes_count"] += 1
    official_value = _safe_float(row.get("official_final_value"))
    if official_value is not None:
        stats["official_values"].append(float(official_value))
    rounded_value = _safe_float(row.get("rounded_official_final_value"))
    if rounded_value is not None:
        stats["rounded_official_values"].append(float(rounded_value))
    threshold = _safe_float(row.get("threshold"))
    if threshold is not None:
        stats["thresholds"].append(float(threshold))
    probability = _safe_float(row.get("prediction_probability"))
    if probability is not None:
        stats["probability_predictions"].append(float(probability))
        _add_count(stats["prediction_sources"], row.get("prediction_probability_source"))
    brier_value = _safe_float(row.get("brier_contribution"))
    if brier_value is not None:
        stats["brier_values"].append(float(brier_value))
    log_loss_value = _safe_float(row.get("log_loss_contribution"))
    if log_loss_value is not None:
        stats["log_loss_values"].append(float(log_loss_value))
    pnl = _safe_float(row.get("resolved_pnl_per_share"))
    if pnl is not None:
        stats["resolved_pnls"].append(float(pnl))
        _add_count(stats["entry_price_sources"], row.get("entry_price_source"))


def _source_counts(mapping: Dict[str, int], key_name: str) -> List[Dict[str, Any]]:
    return [
        {key_name: key, "count": value}
        for key, value in sorted(mapping.items(), key=lambda item: (-item[1], item[0]))
    ]


def _stats_summary(group_type: str, group_key: str, stats: Dict[str, Any]) -> Dict[str, Any]:
    sample_count = int(stats["sample_count"])
    yes_count = int(stats["yes_count"])
    probability_count = len(stats["probability_predictions"])
    pnl_count = len(stats["resolved_pnls"])
    return {
        "group_type": group_type,
        "group_key": group_key,
        "sample_count": sample_count,
        "yes_count": yes_count,
        "no_count": sample_count - yes_count,
        "yes_rate": _rounded(yes_count / sample_count if sample_count else None),
        "mean_official_final_value": _rounded(_mean(stats["official_values"])),
        "min_official_final_value": _rounded(min(stats["official_values"]) if stats["official_values"] else None),
        "max_official_final_value": _rounded(max(stats["official_values"]) if stats["official_values"] else None),
        "mean_rounded_official_final_value": _rounded(_mean(stats["rounded_official_values"])),
        "mean_threshold": _rounded(_mean(stats["thresholds"])),
        "probability_score_count": probability_count,
        "mean_prediction_probability": _rounded(_mean(stats["probability_predictions"])),
        "brier_score": _rounded(_mean(stats["brier_values"])),
        "log_loss": _rounded(_mean(stats["log_loss_values"])),
        "prediction_probability_sources": _source_counts(
            stats["prediction_sources"],
            "source",
        ),
        "resolved_pnl_count": pnl_count,
        "mean_resolved_pnl_per_share": _rounded(_mean(stats["resolved_pnls"])),
        "entry_price_sources": _source_counts(stats["entry_price_sources"], "source"),
    }


def _status_counts(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        status = _text(row.get("status")) or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return [
        {"status": status, "count": count}
        for status, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _build_calibration_row(record: Dict[str, Any], audit: Dict[str, Any]) -> Dict[str, Any]:
    probability, probability_source = _probability_prediction(record)
    entry_price, entry_price_source = _entry_price(record)
    expected_yes = audit.get("expected_yes")
    settled_yes_payout = _safe_float(audit.get("settled_yes_payout"))
    brier_value: Optional[float] = None
    log_loss_value: Optional[float] = None
    if probability is not None and isinstance(expected_yes, bool):
        brier_value = _brier(probability, expected_yes)
        log_loss_value = _log_loss(probability, expected_yes)

    resolved_pnl: Optional[float] = None
    if entry_price is not None and settled_yes_payout is not None:
        resolved_pnl = float(settled_yes_payout) - float(entry_price)

    return {
        "schema_version": SETTLEMENT_CALIBRATION_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "status": "calibrated",
        "market_id": record.get("market_id"),
        "market_slug": record.get("market_slug"),
        "event_slug": record.get("event_slug"),
        "city": audit.get("city"),
        "target_date": audit.get("target_date"),
        "station_code": audit.get("station_code"),
        "settlement_source": audit.get("settlement_source"),
        "bucket_label": audit.get("bucket_label"),
        "bucket_type": audit.get("bucket_type"),
        "threshold": audit.get("threshold"),
        "upper_threshold": audit.get("upper_threshold"),
        "official_final_value": audit.get("official_final_value"),
        "rounded_official_final_value": audit.get("rounded_official_final_value"),
        "official_final_value_source": audit.get("official_final_value_source"),
        "expected_yes": expected_yes,
        "settled_yes_payout": settled_yes_payout,
        "prediction_probability": _rounded(probability),
        "prediction_probability_source": record.get("historical_prediction_probability_source") or probability_source,
        "brier_contribution": _rounded(brier_value),
        "log_loss_contribution": _rounded(log_loss_value),
        "entry_price": _rounded(entry_price),
        "entry_price_source": record.get("historical_entry_price_source") or entry_price_source,
        "resolved_pnl_per_share": _rounded(resolved_pnl),
        "resolved_pnl_available": resolved_pnl is not None,
        "resolved_pnl_gap_reason": None if resolved_pnl is not None else "missing_historical_entry_price",
        "historical_evidence_supplement_applied": record.get("historical_evidence_supplement_applied") is True,
        "historical_evidence_source": record.get("historical_evidence_source"),
        "historical_evidence_available_at": record.get("historical_evidence_available_at"),
        "historical_evidence_orderbook_snapshot_id": record.get("historical_evidence_orderbook_snapshot_id"),
        "historical_evidence_no_lookahead": record.get("historical_evidence_no_lookahead") is True,
    }


def build_settlement_calibration_report(
    records: Iterable[Dict[str, Any]],
    *,
    historical_evidence_supplements: Optional[Iterable[Dict[str, Any]]] = None,
    min_official_truth_samples: int = 30,
    min_probability_score_samples: int = 30,
    min_resolved_pnl_samples: int = 10,
    min_official_truth_coverage: float = 0.80,
    max_sample_rows: int = 20,
) -> Dict[str, Any]:
    historical_evidence_summary: Optional[Dict[str, Any]] = None
    materialized_records = [record for record in records if isinstance(record, dict)]
    if historical_evidence_supplements is not None:
        materialized_records, historical_evidence_summary = apply_historical_evidence_supplements(
            materialized_records,
            supplements=historical_evidence_supplements,
        )
    resolved_records = [
        record
        for record in materialized_records
        if record.get("status") == "resolved"
    ]
    audits = [audit_settlement_truth_record(record) for record in resolved_records]
    passed_pairs = [
        (record, audit)
        for record, audit in zip(resolved_records, audits)
        if audit.get("status") == "pass"
    ]
    mismatches = [audit for audit in audits if audit.get("status") == "mismatch"]
    gaps = [audit for audit in audits if audit.get("status") not in {"pass", "mismatch"}]
    calibration_rows = [
        _build_calibration_row(record, audit)
        for record, audit in passed_pairs
    ]

    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in calibration_rows:
        for group in _group_keys(row):
            stats = groups.setdefault(group, _base_stats())
            _update_stats(stats, row)
    group_summaries = [
        _stats_summary(group_type, group_key, stats)
        for (group_type, group_key), stats in groups.items()
    ]
    group_summaries.sort(
        key=lambda row: (
            0 if row["group_type"] == "global" else 1,
            -int(row["sample_count"]),
            row["group_type"],
            row["group_key"],
        )
    )

    record_count = len(resolved_records)
    pass_count = len(passed_pairs)
    official_truth_coverage = pass_count / record_count if record_count else 0.0
    probability_score_count = len(
        [row for row in calibration_rows if row.get("prediction_probability") is not None]
    )
    resolved_pnl_count = len(
        [row for row in calibration_rows if row.get("resolved_pnl_per_share") is not None]
    )

    blockers: List[str] = []
    if not record_count:
        blockers.append("settlement_calibration_no_resolved_records")
    if mismatches:
        blockers.append("settlement_calibration_truth_mismatch")
    if pass_count < int(min_official_truth_samples):
        blockers.append(
            f"insufficient_official_truth_samples_{pass_count}_of_{int(min_official_truth_samples)}"
        )
    if official_truth_coverage < float(min_official_truth_coverage):
        blockers.append("official_truth_coverage_below_min")
    if probability_score_count < int(min_probability_score_samples):
        blockers.append(
            f"insufficient_probability_score_samples_{probability_score_count}_of_{int(min_probability_score_samples)}"
        )
    if resolved_pnl_count < int(min_resolved_pnl_samples):
        blockers.append(
            f"insufficient_resolved_pnl_samples_{resolved_pnl_count}_of_{int(min_resolved_pnl_samples)}"
        )

    if not blockers:
        hard_conclusion = "settlement_calibration_ready_diagnostic_only"
    else:
        hard_conclusion = blockers[0]

    global_group = next(
        (row for row in group_summaries if row["group_type"] == "global" and row["group_key"] == "all"),
        None,
    )
    return {
        "schema_version": SETTLEMENT_CALIBRATION_SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "hard_conclusion": hard_conclusion,
        "blockers": blockers,
        "record_count": record_count,
        "official_truth_sample_count": pass_count,
        "official_truth_coverage": _rounded(official_truth_coverage),
        "mismatch_count": len(mismatches),
        "gap_count": len(gaps),
        "probability_evidence_available": probability_score_count > 0,
        "probability_score_sample_count": probability_score_count,
        "probability_score_gap_reason": (
            None
            if probability_score_count >= int(min_probability_score_samples)
            else "missing_historical_forecast_probability"
        ),
        "resolved_pnl_available": resolved_pnl_count > 0,
        "resolved_pnl_sample_count": resolved_pnl_count,
        "resolved_pnl_gap_reason": (
            None
            if resolved_pnl_count >= int(min_resolved_pnl_samples)
            else "missing_historical_entry_price"
        ),
        "historical_evidence_supplement_summary": historical_evidence_summary,
        "by_audit_status": _status_counts(audits),
        "global_calibration": global_group,
        "group_count": len(group_summaries),
        "groups": group_summaries,
        "mismatch_samples": mismatches[: max(0, int(max_sample_rows))],
        "gap_samples": gaps[: max(0, int(max_sample_rows))],
        "calibration_rows": calibration_rows,
    }


def build_settlement_calibration_report_from_dir(
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    *,
    official_value_supplements_path: Optional[str | Path] = None,
    historical_evidence_supplements_path: Optional[str | Path] = None,
    min_official_truth_samples: int = 30,
    min_probability_score_samples: int = 30,
    min_resolved_pnl_samples: int = 10,
    min_official_truth_coverage: float = 0.80,
    max_sample_rows: int = 20,
) -> Dict[str, Any]:
    return build_settlement_calibration_report(
        load_closed_backfill_records_with_snapshot_supplements(
            backfill_dir,
            official_value_supplements_path=official_value_supplements_path,
        ),
        historical_evidence_supplements=(
            load_historical_evidence_supplements(historical_evidence_supplements_path)
            if historical_evidence_supplements_path
            else None
        ),
        min_official_truth_samples=min_official_truth_samples,
        min_probability_score_samples=min_probability_score_samples,
        min_resolved_pnl_samples=min_resolved_pnl_samples,
        min_official_truth_coverage=min_official_truth_coverage,
        max_sample_rows=max_sample_rows,
    )
