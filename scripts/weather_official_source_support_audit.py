#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.weather.official_value_backfill import build_official_value_supplement  # noqa: E402
from src.weather.station_registry import SUPPORTED_OFFICIAL_SOURCE_ADAPTERS, active_supported_metar_station_manifest  # noqa: E402


SCHEMA_VERSION = "polyweather_official_source_support_audit.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    try:
        parsed = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_rows_from_report(path: str | Path) -> List[Dict[str, Any]]:
    payload = _load_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return [row for row in rows or [] if isinstance(row, dict)]


def _load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _spec(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("settlement_spec")
    return value if isinstance(value, dict) else {}


def _field(row: Dict[str, Any], field: str) -> str:
    spec = _spec(row)
    for source in (row, spec):
        text = _text(source.get(field))
        if text:
            return text
    if field == "station_code":
        return _text(row.get("settlement_station_code") or spec.get("settlement_station_code")).upper()
    return ""


def _bucket_type(row: Dict[str, Any]) -> str:
    return _field(row, "bucket_type").lower()


def _threshold(row: Dict[str, Any]) -> Optional[float]:
    value = _safe_float(_spec(row).get("threshold"))
    return value if value is not None else _safe_float(row.get("threshold"))


def _upper_threshold(row: Dict[str, Any]) -> Optional[float]:
    value = _safe_float(_spec(row).get("upper_threshold"))
    return value if value is not None else _safe_float(row.get("upper_threshold"))


def _rounded_official(value: float, row: Dict[str, Any]) -> float:
    rounding = _text(_spec(row).get("rounding") or row.get("rounding")).lower()
    if rounding in {"integer_nearest", "nearest_integer", ""}:
        return float(round(value))
    return float(value)


def _yes_wins(row: Dict[str, Any], official_value: float) -> Optional[bool]:
    bucket = _bucket_type(row)
    threshold = _threshold(row)
    upper = _upper_threshold(row)
    if threshold is None:
        return None
    rounded = _rounded_official(float(official_value), row)
    if bucket == "eq":
        return rounded == float(threshold)
    if bucket == "le":
        return rounded <= float(threshold)
    if bucket == "ge":
        return rounded >= float(threshold)
    if bucket == "range" and upper is not None:
        return float(threshold) <= rounded <= float(upper)
    return None


def _closed_truth_matches(rows: Iterable[Dict[str, Any]], official_value: Optional[float]) -> Optional[bool]:
    if official_value is None:
        return None
    checked = 0
    for row in rows:
        winning = _text(row.get("winning_outcome") or row.get("winning_side")).lower()
        yes_wins = _yes_wins(row, official_value)
        if yes_wins is None or winning not in {"yes", "no"}:
            continue
        checked += 1
        if (winning == "yes") != bool(yes_wins):
            return False
    if checked == 0:
        return None
    return True


def _record_for_fetch(*, station_code: str, settlement_source: str, target_date: str, city: str = "", unit: str = "C") -> Dict[str, Any]:
    return {
        "city": city,
        "target_date": target_date,
        "settlement_station_code": station_code,
        "settlement_source": settlement_source,
        "settlement_spec": {
            "station_code": station_code,
            "settlement_source": settlement_source,
            "target_date": target_date,
            "unit": unit,
        },
    }


def _fetch_ready(
    *,
    station_code: str,
    target_date: str,
    settlement_source: str,
    city: str,
    collector: Any = None,
    fetch_external: bool = False,
) -> Dict[str, Any]:
    record = _record_for_fetch(
        station_code=station_code,
        settlement_source=settlement_source,
        target_date=target_date,
        city=city,
    )
    return build_official_value_supplement(
        record,
        collector=collector,
        fetch_external=fetch_external,
    )


def _counter_rows(counter: Counter[str], field: str) -> List[Dict[str, Any]]:
    return [{field: key, "row_count": value} for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]


def build_official_source_support_audit(
    *,
    active_rows: Iterable[Dict[str, Any]],
    closed_rows: Iterable[Dict[str, Any]],
    collector: Any = None,
    fetch_external: bool = False,
) -> Dict[str, Any]:
    active_rows = [row for row in active_rows if isinstance(row, dict)]
    closed_rows = [row for row in closed_rows if isinstance(row, dict)]
    manifest = active_supported_metar_station_manifest(active_rows)
    legacy_unsupported = manifest.get("legacy_metar_only_unsupported_eq_rows_by_source") or manifest.get("unsupported_eq_rows_by_source") or []

    active_eq_by_key: Dict[tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in active_rows:
        if _bucket_type(row) != "eq":
            continue
        station = _field(row, "station_code").upper()
        source = _field(row, "settlement_source").lower()
        target_date = _field(row, "target_date")
        if station and source and target_date:
            active_eq_by_key[(station, source, target_date)].append(row)

    closed_by_key: Dict[tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in closed_rows:
        station = _field(row, "station_code").upper()
        source = _field(row, "settlement_source").lower()
        target_date = _field(row, "target_date")
        if station and source and target_date:
            closed_by_key[(station, source, target_date)].append(row)

    audit_keys = set(active_eq_by_key)
    audit_keys.update(key for key in closed_by_key if key[1] not in {"metar"})
    source_audits: List[Dict[str, Any]] = []
    by_source = Counter()
    supported_after_sources = Counter()
    noaa_supported = False

    for station, source, target_date in sorted(audit_keys):
        active_group = active_eq_by_key.get((station, source, target_date), [])
        closed_group = closed_by_key.get((station, source, target_date), [])
        city = _field((active_group or closed_group or [{}])[0], "city")
        row_count = len(active_group)
        by_source[source] += row_count
        metar_supplement = _fetch_ready(
            station_code=station,
            target_date=target_date,
            settlement_source="metar",
            city=city,
            collector=collector,
            fetch_external=fetch_external,
        )
        source_supplement = _fetch_ready(
            station_code=station,
            target_date=target_date,
            settlement_source=source,
            city=city,
            collector=collector,
            fetch_external=fetch_external,
        )
        source_value = _safe_float(source_supplement.get("official_final_value"))
        truth_match = _closed_truth_matches(closed_group, source_value)
        can_fetch_source = source_supplement.get("status") == "ready"
        if source in SUPPORTED_OFFICIAL_SOURCE_ADAPTERS and can_fetch_source:
            supported_after_sources[source] += row_count or len(closed_group)
        if source == "noaa" and can_fetch_source and truth_match is True:
            noaa_supported = True
        recommended_adapter = None
        if source == "noaa":
            recommended_adapter = "noaa_station_observation"
        elif source in SUPPORTED_OFFICIAL_SOURCE_ADAPTERS:
            recommended_adapter = SUPPORTED_OFFICIAL_SOURCE_ADAPTERS[source]
        reason = "supported_via_station_official_adapter" if can_fetch_source else (
            source_supplement.get("gap_reason") or "unsupported_or_unavailable_source_adapter"
        )
        source_audits.append(
            {
                "station_code": station,
                "target_date": target_date,
                "settlement_source": source,
                "active_eq_row_count": row_count,
                "closed_backfill_market_count": len(closed_group),
                "can_fetch_via_metar": metar_supplement.get("status") == "ready",
                "can_fetch_via_metar_reason": metar_supplement.get("gap_reason"),
                "can_fetch_via_noaa_endpoint": source_supplement.get("status") == "ready" if source == "noaa" else None,
                "can_fetch_via_noaa_endpoint_reason": source_supplement.get("gap_reason") if source == "noaa" else None,
                "can_fetch_intraday": bool(can_fetch_source),
                "official_final_value": source_value,
                "official_final_value_source": source_supplement.get("official_final_value_source"),
                "official_final_value_source_code": source_supplement.get("official_final_value_source_code"),
                "official_truth_match_with_closed_backfill_if_available": truth_match,
                "recommended_adapter": recommended_adapter,
                "safe_to_support_source": bool(
                    can_fetch_source
                    and (
                        truth_match is True
                        or (source != "noaa" and truth_match is not False)
                    )
                ),
                "safe_to_reclassify_source": False,
                "reason": reason,
                "source_supplement_status": source_supplement.get("status"),
                "source_gap_detail": source_supplement.get("gap_detail"),
                "sample_active_market_slugs": [_text(row.get("market_slug")) for row in active_group[:5] if _text(row.get("market_slug"))],
                "sample_closed_market_slugs": [_text(row.get("market_slug")) for row in closed_group[:5] if _text(row.get("market_slug"))],
            }
        )

    supported_before = int(manifest.get("active_supported_metar_station_count") or 0)
    supported_after = int(manifest.get("active_supported_official_station_count") or supported_before)
    noaa_rows = sum(int(row.get("row_count") or 0) for row in legacy_unsupported if row.get("settlement_source") == "noaa")
    verdict = (
        "noaa_ltfm_supported_via_noaa_station_observation_adapter"
        if noaa_supported
        else "noaa_ltfm_not_supported_yet"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fetch_external": bool(fetch_external),
        "unsupported_source_rows_by_source": legacy_unsupported,
        "active_supported_station_count_before": supported_before,
        "active_supported_station_count_after": supported_after,
        "active_supported_metar_station_count": supported_before,
        "active_supported_official_station_count": supported_after,
        "supported_official_station_codes": manifest.get("supported_official_station_codes") or [],
        "legacy_metar_station_codes": manifest.get("legacy_metar_station_codes") or manifest.get("station_codes") or [],
        "noaa_ltfm_support_verdict": verdict,
        "noaa_active_eq_row_count": noaa_rows,
        "source_audit_count": len(source_audits),
        "source_audits": source_audits,
        "rows_by_active_settlement_source": _counter_rows(by_source, "settlement_source"),
        "rows_by_supported_after_source": _counter_rows(supported_after_sources, "settlement_source"),
        "station_manifest": manifest,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit NOAA/non-METAR official source support for paper-only weather rows.")
    parser.add_argument("--active-signal-report", default="evidence/eq_dead_no/eq_dead_no_signal_report.json")
    parser.add_argument("--closed-market-path", default="evidence/weather_backfill_local/closed_markets.jsonl")
    parser.add_argument("--summary-output", default="evidence/official_source_support_audit.json")
    parser.add_argument("--fetch-external", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_official_source_support_audit(
        active_rows=_load_rows_from_report(args.active_signal_report),
        closed_rows=_load_jsonl(args.closed_market_path),
        fetch_external=args.fetch_external,
    )
    output = Path(args.summary_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
