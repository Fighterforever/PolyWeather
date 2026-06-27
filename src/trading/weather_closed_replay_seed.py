from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_market_catalog import build_temperature_settlement_spec
from src.trading.weather_paper_journal import _safe_float, load_jsonl
from src.weather.official_value_backfill import (
    apply_official_value_supplements,
    load_official_value_supplements,
)
from src.weather.settlement_truth import load_official_temperature_value


CLOSED_REPLAY_SEED_SCHEMA_VERSION = "polyweather_weather_closed_replay_seed.v1"


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _bucket_type(record: Dict[str, Any]) -> str:
    parsed = record.get("parsed_temperature_spec")
    if isinstance(parsed, dict):
        value = str(parsed.get("comparator") or "").strip().lower()
        if value:
            return value
    label = str(record.get("bucket_label") or "").strip()
    if label.startswith(">="):
        return "ge"
    if label.startswith("<="):
        return "le"
    if label.startswith("="):
        return "eq"
    if "-" in label:
        return "range"
    return "unknown"


def _token_id_for_outcome(record: Dict[str, Any], outcome: str) -> Optional[str]:
    token_id_by_outcome = record.get("token_id_by_outcome")
    if isinstance(token_id_by_outcome, dict):
        token_id = str(token_id_by_outcome.get(outcome) or "").strip()
        if token_id:
            return token_id
    if outcome == str(record.get("winning_outcome") or "").strip():
        token_id = str(record.get("winning_token_id") or "").strip()
        if token_id:
            return token_id
    return None


def _market_key(record: Dict[str, Any]) -> str:
    return str(record.get("market_id") or record.get("market_slug") or "").strip()


def _snapshot_supplements_from_payload(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload, dict) else []
    supplements: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = _market_key(row)
        if not key:
            continue
        outcome = str(row.get("outcome") or row.get("side") or "").strip()
        if not outcome:
            continue
        supplement = supplements.setdefault(
            key,
            {
                "token_id_by_outcome": {},
                "settled_probability_by_outcome": {},
                "resolution_source": None,
                "settlement_spec": None,
                "rule_hash": None,
                "official_final_value": None,
                "end_date": None,
            },
        )
        token_id = str(row.get("token_id") or "").strip()
        if token_id:
            supplement["token_id_by_outcome"][outcome] = token_id
        payout = _safe_float(row.get("market_probability") if row.get("market_probability") is not None else row.get("price"))
        if payout is not None:
            supplement["settled_probability_by_outcome"][outcome] = float(payout)
        if not supplement.get("resolution_source") and row.get("resolution_source"):
            supplement["resolution_source"] = row.get("resolution_source")
        settlement_spec = row.get("settlement_spec") if isinstance(row.get("settlement_spec"), dict) else None
        if settlement_spec and not supplement.get("settlement_spec"):
            supplement["settlement_spec"] = settlement_spec
        rule_hash = row.get("rule_hash") or (settlement_spec or {}).get("rule_hash")
        if rule_hash and not supplement.get("rule_hash"):
            supplement["rule_hash"] = rule_hash
        if supplement.get("official_final_value") is None and row.get("official_final_value") is not None:
            supplement["official_final_value"] = row.get("official_final_value")
        if supplement.get("end_date") is None and row.get("end_date") is not None:
            supplement["end_date"] = row.get("end_date")
    return supplements


def _load_snapshot_supplements(backfill_dir: str | Path) -> Dict[str, Dict[str, Any]]:
    snapshot_dir = Path(backfill_dir) / "snapshots"
    supplements: Dict[str, Dict[str, Any]] = {}
    for path in sorted(snapshot_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = raw.get("payload") if isinstance(raw, dict) and isinstance(raw.get("payload"), dict) else raw
        for key, supplement in _snapshot_supplements_from_payload(payload).items():
            current = supplements.setdefault(
                key,
                {
                    "token_id_by_outcome": {},
                    "settled_probability_by_outcome": {},
                    "resolution_source": None,
                    "settlement_spec": None,
                    "rule_hash": None,
                    "official_final_value": None,
                    "end_date": None,
                },
            )
            current["token_id_by_outcome"].update(supplement.get("token_id_by_outcome") or {})
            current["settled_probability_by_outcome"].update(supplement.get("settled_probability_by_outcome") or {})
            for field in ("resolution_source", "settlement_spec", "rule_hash", "official_final_value", "end_date"):
                if current.get(field) is None and supplement.get(field) is not None:
                    current[field] = supplement.get(field)
    return supplements


def supplement_closed_backfill_records_from_snapshots(
    backfill_records: Iterable[Dict[str, Any]],
    *,
    snapshot_supplements: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    supplemented: List[Dict[str, Any]] = []
    for record in backfill_records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        supplement = snapshot_supplements.get(_market_key(row))
        supplemented_fields: List[str] = []
        if supplement:
            token_map = row.get("token_id_by_outcome") if isinstance(row.get("token_id_by_outcome"), dict) else {}
            merged_token_map = dict(token_map)
            merged_token_map.update(supplement.get("token_id_by_outcome") or {})
            if merged_token_map and merged_token_map != token_map:
                row["token_id_by_outcome"] = merged_token_map
                supplemented_fields.append("token_id_by_outcome")

            payout_map = row.get("settled_probability_by_outcome")
            payout_map = dict(payout_map) if isinstance(payout_map, dict) else {}
            merged_payout_map = dict(payout_map)
            merged_payout_map.update(supplement.get("settled_probability_by_outcome") or {})
            if merged_payout_map and merged_payout_map != payout_map:
                row["settled_probability_by_outcome"] = merged_payout_map
                supplemented_fields.append("settled_probability_by_outcome")

            for field in ("resolution_source", "settlement_spec", "rule_hash", "official_final_value", "end_date"):
                if row.get(field) is None and supplement.get(field) is not None:
                    row[field] = supplement.get(field)
                    supplemented_fields.append(field)
        if supplemented_fields:
            row["snapshot_supplement_applied"] = True
            row["snapshot_supplemented_fields"] = supplemented_fields
        supplemented.append(row)
    return supplemented


def _record_settlement_spec(record: Dict[str, Any]) -> Dict[str, Any]:
    value = record.get("settlement_spec")
    return value if isinstance(value, dict) else {}


def supplement_closed_backfill_records_from_official_observations(
    backfill_records: Iterable[Dict[str, Any]],
    *,
    repository: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    supplemented: List[Dict[str, Any]] = []
    for record in backfill_records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        if row.get("official_final_value") is not None:
            supplemented.append(row)
            continue
        parsed = row.get("parsed_temperature_spec") if isinstance(row.get("parsed_temperature_spec"), dict) else None
        if not parsed:
            row["official_final_value_gap_reason"] = "unsupported_non_temperature_market"
            supplemented.append(row)
            continue
        spec = _record_settlement_spec(row)
        city = str(row.get("city") or parsed.get("city") or spec.get("city") or "").strip().lower()
        target_date = str(row.get("target_date") or parsed.get("target_date") or spec.get("target_date") or "").strip()
        result = load_official_temperature_value(
            city=city,
            target_date=target_date,
            station_code=spec.get("station_code"),
            settlement_source=spec.get("settlement_source") or row.get("settlement_source"),
            repository=repository,
        )
        if result.get("status") == "ready" and result.get("official_final_value") is not None:
            row["official_final_value"] = result.get("official_final_value")
            row["official_final_value_source"] = result.get("source")
            row["official_final_value_source_code"] = result.get("source_code")
            row["official_final_value_station_code"] = result.get("station_code")
            row["official_final_value_observed_at"] = result.get("max_observed_at")
            row["official_final_value_observation_count"] = result.get("observation_count")
            row["official_observation_supplement_applied"] = True
        else:
            row["official_final_value_gap_reason"] = result.get("status") or "missing_official_observation"
            row["official_final_value_gap_detail"] = {
                key: result.get(key)
                for key in (
                    "city",
                    "target_date",
                    "station_code",
                    "source_code",
                    "attempted_sources",
                )
                if result.get(key) is not None
            }
        supplemented.append(row)
    return supplemented


def _truth_group_key(record: Dict[str, Any]) -> tuple[str, str, str]:
    parsed = record.get("parsed_temperature_spec") if isinstance(record.get("parsed_temperature_spec"), dict) else {}
    return (
        str(record.get("city") or parsed.get("city") or "unknown").strip().lower(),
        str(record.get("target_date") or parsed.get("target_date") or "unknown").strip(),
        str(parsed.get("unit") or "unknown").strip().upper(),
    )


def _yes_payout(record: Dict[str, Any]) -> Optional[float]:
    settled = record.get("settled_probability_by_outcome")
    if not isinstance(settled, dict):
        return None
    return _safe_float(settled.get("Yes") if "Yes" in settled else settled.get("yes"))


def _with_rebuilt_settlement_specs(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for record in records:
        row = dict(record)
        parsed = row.get("parsed_temperature_spec") if isinstance(row.get("parsed_temperature_spec"), dict) else None
        if parsed and (not row.get("rule_hash") or not isinstance(row.get("settlement_spec"), dict)):
            spec, _reasons = build_temperature_settlement_spec(
                row,
                city=parsed.get("city") or row.get("city"),
                target_date=parsed.get("target_date") or row.get("target_date"),
                bucket_type=str(parsed.get("comparator") or ""),
                threshold=_safe_float(parsed.get("threshold")),
                upper_threshold=_safe_float(parsed.get("upper_threshold")),
                unit=str(parsed.get("unit") or "C"),
            )
            if spec is not None:
                fields: List[str] = []
                if not isinstance(row.get("settlement_spec"), dict):
                    row["settlement_spec"] = spec.to_dict()
                    fields.append("settlement_spec")
                if not row.get("rule_hash"):
                    row["rule_hash"] = spec.rule_hash
                    fields.append("rule_hash")
                if not row.get("rule_text"):
                    row["rule_text"] = spec.rule_text
                    fields.append("rule_text")
                if fields:
                    row["settlement_spec_rebuilt"] = True
                    row["settlement_spec_rebuilt_fields"] = fields
        enriched.append(row)
    return enriched


def _with_market_inferred_final_values(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [dict(record) for record in records if isinstance(record, dict)]
    exact_yes_by_group: Dict[tuple[str, str, str], set[float]] = {}
    for row in rows:
        parsed = row.get("parsed_temperature_spec") if isinstance(row.get("parsed_temperature_spec"), dict) else None
        if not parsed or str(parsed.get("comparator") or "").lower() != "eq":
            continue
        threshold = _safe_float(parsed.get("threshold"))
        yes_payout = _yes_payout(row)
        if threshold is None or yes_payout is None or yes_payout < 0.999:
            continue
        exact_yes_by_group.setdefault(_truth_group_key(row), set()).add(float(threshold))

    inferred_by_group = {
        key: next(iter(values))
        for key, values in exact_yes_by_group.items()
        if len(values) == 1
    }
    for row in rows:
        if row.get("official_final_value") is not None:
            continue
        inferred = inferred_by_group.get(_truth_group_key(row))
        if inferred is None:
            continue
        row["market_inferred_final_value"] = inferred
        row["market_inferred_final_value_source"] = "closed_exact_bucket_family"
    return rows


def enrich_closed_backfill_truth(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _with_market_inferred_final_values(_with_rebuilt_settlement_specs(records))


def build_closed_replay_seed_records(
    backfill_records: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    seeds: List[Dict[str, Any]] = []
    for record in enrich_closed_backfill_truth(backfill_records):
        if not isinstance(record, dict) or record.get("status") != "resolved":
            continue
        settled = record.get("settled_probability_by_outcome")
        if not isinstance(settled, dict):
            settled = {}
        outcomes = [str(outcome or "").strip() for outcome in record.get("outcomes") or []]
        for outcome in outcomes:
            if not outcome:
                continue
            payout = _safe_float(settled.get(outcome))
            token_id = _token_id_for_outcome(record, outcome)
            if token_id is None or payout is None:
                continue
            seeds.append(
                {
                    "schema_version": CLOSED_REPLAY_SEED_SCHEMA_VERSION,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_gate_excluded": True,
                    "diagnostic_only": True,
                    "source": "closed_backfill",
                    "record_id": record.get("record_id"),
                    "market_id": record.get("market_id"),
                    "market_slug": record.get("market_slug"),
                    "event_slug": record.get("event_slug"),
                    "question": record.get("question"),
                    "city": record.get("city"),
                    "target_date": record.get("target_date"),
                    "bucket_label": record.get("bucket_label"),
                    "bucket_type": _bucket_type(record),
                    "outcome": outcome,
                    "side": str(outcome).lower(),
                    "token_id": token_id,
                    "payout": float(payout),
                    "winning": float(payout) >= 0.999,
                    "winning_outcome": record.get("winning_outcome"),
                    "winning_token_id": record.get("winning_token_id"),
                    "resolution_source": record.get("resolution_source") or "closed_backfill",
                    "resolution_rule_hash": record.get("rule_hash"),
                    "official_final_value": record.get("official_final_value"),
                    "official_final_value_source": record.get("official_final_value_source"),
                    "official_final_value_source_code": record.get("official_final_value_source_code"),
                    "official_final_value_station_code": record.get("official_final_value_station_code"),
                    "official_final_value_observed_at": record.get("official_final_value_observed_at"),
                    "market_inferred_final_value": record.get("market_inferred_final_value"),
                    "market_inferred_final_value_source": record.get("market_inferred_final_value_source"),
                    "settlement_spec": record.get("settlement_spec"),
                }
            )
    return seeds


def _market_token_gap(record: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = [str(outcome or "").strip() for outcome in record.get("outcomes") or [] if str(outcome or "").strip()]
    mapped = [
        outcome
        for outcome in outcomes
        if _token_id_for_outcome(record, outcome)
    ]
    settled = record.get("settled_probability_by_outcome")
    settled = settled if isinstance(settled, dict) else {}
    payout_ready = [
        outcome
        for outcome in outcomes
        if _safe_float(settled.get(outcome)) is not None
    ]
    return {
        "market_slug": record.get("market_slug"),
        "city": record.get("city"),
        "bucket_type": _bucket_type(record),
        "outcome_count": len(outcomes),
        "token_mapped_count": len(mapped),
        "payout_ready_count": len(payout_ready),
        "missing_token_outcomes": [outcome for outcome in outcomes if outcome not in mapped],
        "missing_payout_outcomes": [outcome for outcome in outcomes if outcome not in payout_ready],
        "has_rule_hash": bool(record.get("rule_hash")),
        "has_official_final_value": record.get("official_final_value") is not None,
        "official_final_value_gap_reason": record.get("official_final_value_gap_reason"),
        "official_final_value_gap_detail": record.get("official_final_value_gap_detail"),
    }


def build_closed_replay_seed_report(
    backfill_records: Iterable[Dict[str, Any]],
    *,
    max_gap_samples: int = 10,
    supplement_official_observations: bool = False,
    official_value_repository: Optional[Any] = None,
    official_value_supplements: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    input_rows = enrich_closed_backfill_truth(record for record in backfill_records if isinstance(record, dict))
    if official_value_supplements is not None:
        input_rows = apply_official_value_supplements(
            input_rows,
            supplements=official_value_supplements,
        )
    if supplement_official_observations:
        input_rows = supplement_closed_backfill_records_from_official_observations(
            input_rows,
            repository=official_value_repository,
        )
    rows = enrich_closed_backfill_truth(input_rows)
    resolved = [record for record in rows if record.get("status") == "resolved"]
    parsed_temperature = [
        record
        for record in resolved
        if isinstance(record.get("parsed_temperature_spec"), dict)
    ]
    seeds = build_closed_replay_seed_records(resolved)
    gaps = [_market_token_gap(record) for record in resolved]
    complete = [
        gap
        for gap in gaps
        if gap["outcome_count"] > 0
        and gap["token_mapped_count"] == gap["outcome_count"]
        and gap["payout_ready_count"] == gap["outcome_count"]
    ]
    partial = [
        gap
        for gap in gaps
        if gap not in complete
        and (gap["token_mapped_count"] > 0 or gap["payout_ready_count"] > 0)
    ]
    missing_token_map = [gap for gap in gaps if gap["token_mapped_count"] < gap["outcome_count"]]
    missing_payout = [gap for gap in gaps if gap["payout_ready_count"] < gap["outcome_count"]]
    missing_rule_hash = [record for record in resolved if not record.get("rule_hash")]
    missing_official_value = [
        record
        for record in resolved
        if record.get("official_final_value") is None
    ]
    unsupported_closed_markets = [
        record
        for record in resolved
        if not isinstance(record.get("parsed_temperature_spec"), dict)
    ]
    temperature_missing_rule_hash = [
        record
        for record in parsed_temperature
        if not record.get("rule_hash")
    ]
    temperature_missing_official_value = [
        record
        for record in parsed_temperature
        if record.get("official_final_value") is None
    ]
    market_inferred_final_value = [
        record
        for record in resolved
        if record.get("market_inferred_final_value") is not None
    ]
    snapshot_supplemented = [
        record
        for record in resolved
        if record.get("snapshot_supplement_applied") is True
    ]
    official_observation_supplemented = [
        record
        for record in resolved
        if record.get("official_observation_supplement_applied") is True
    ]
    official_value_supplemented = [
        record
        for record in resolved
        if record.get("official_value_supplement_applied") is True
    ]
    official_observation_gaps = [
        record
        for record in resolved
        if record.get("official_final_value_gap_reason")
    ]
    if not resolved:
        hard_conclusion = "closed_replay_seed_no_resolved_markets"
    elif not seeds:
        hard_conclusion = "closed_replay_seed_no_token_payouts"
    elif missing_token_map or missing_payout:
        hard_conclusion = "closed_replay_seed_partial_token_coverage"
    elif temperature_missing_rule_hash:
        hard_conclusion = "closed_replay_seed_missing_rule_hash"
    elif temperature_missing_official_value:
        hard_conclusion = "closed_replay_seed_missing_official_final_value"
    else:
        hard_conclusion = "closed_replay_seed_ready"

    return {
        "schema_version": CLOSED_REPLAY_SEED_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate_excluded": True,
        "diagnostic_only": True,
        "hard_conclusion": hard_conclusion,
        "backfill_record_count": len(rows),
        "resolved_market_count": len(resolved),
        "parsed_temperature_count": len(parsed_temperature),
        "replay_seed_token_count": len(seeds),
        "complete_token_market_count": len(complete),
        "partial_token_market_count": len(partial),
        "missing_token_map_market_count": len(missing_token_map),
        "missing_payout_market_count": len(missing_payout),
        "missing_rule_hash_count": len(missing_rule_hash),
        "missing_official_final_value_count": len(missing_official_value),
        "unsupported_closed_market_count": len(unsupported_closed_markets),
        "temperature_missing_rule_hash_count": len(temperature_missing_rule_hash),
        "temperature_missing_official_final_value_count": len(temperature_missing_official_value),
        "market_inferred_final_value_count": len(market_inferred_final_value),
        "snapshot_supplemented_market_count": len(snapshot_supplemented),
        "official_observation_supplemented_market_count": len(official_observation_supplemented),
        "official_value_supplemented_market_count": len(official_value_supplemented),
        "official_observation_gap_count": len(official_observation_gaps),
        "official_observation_gaps_by_reason": _count_by(
            official_observation_gaps,
            "official_final_value_gap_reason",
            key_name="reason",
        ),
        "by_city": _count_by(resolved, "city", key_name="city"),
        "by_bucket_type": _count_by(
            [{"bucket_type": _bucket_type(record)} for record in resolved],
            "bucket_type",
            key_name="bucket_type",
        ),
        "seed_by_bucket_type": _count_by(seeds, "bucket_type", key_name="bucket_type"),
        "gap_samples": [
            gap
            for gap in gaps
            if gap["token_mapped_count"] < gap["outcome_count"]
            or gap["payout_ready_count"] < gap["outcome_count"]
            or not gap["has_rule_hash"]
            or not gap["has_official_final_value"]
        ][: max(0, int(max_gap_samples))],
        "seeds": seeds,
    }


def build_closed_replay_seed_report_from_dir(
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    *,
    max_gap_samples: int = 10,
    supplement_official_observations: bool = False,
    official_value_repository: Optional[Any] = None,
    official_value_supplements: Optional[Iterable[Dict[str, Any]]] = None,
    official_value_supplements_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    return build_closed_replay_seed_report(
        load_closed_backfill_records_with_snapshot_supplements(
            backfill_dir,
            supplement_official_observations=supplement_official_observations,
            official_value_repository=official_value_repository,
            official_value_supplements=official_value_supplements,
            official_value_supplements_path=official_value_supplements_path,
        ),
        max_gap_samples=max_gap_samples,
    )


def load_closed_backfill_records_with_snapshot_supplements(
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    *,
    supplement_official_observations: bool = False,
    official_value_repository: Optional[Any] = None,
    official_value_supplements: Optional[Iterable[Dict[str, Any]]] = None,
    official_value_supplements_path: Optional[str | Path] = None,
) -> List[Dict[str, Any]]:
    backfill_root = Path(backfill_dir)
    records = enrich_closed_backfill_truth(
        supplement_closed_backfill_records_from_snapshots(
            load_jsonl(backfill_root / "closed_markets.jsonl"),
            snapshot_supplements=_load_snapshot_supplements(backfill_root),
        )
    )
    supplement_rows = list(official_value_supplements or [])
    if official_value_supplements_path:
        supplement_rows.extend(load_official_value_supplements(official_value_supplements_path))
    if supplement_rows:
        records = apply_official_value_supplements(
            records,
            supplements=supplement_rows,
        )
    if supplement_official_observations:
        records = supplement_closed_backfill_records_from_official_observations(
            records,
            repository=official_value_repository,
        )
    return enrich_closed_backfill_truth(records)
