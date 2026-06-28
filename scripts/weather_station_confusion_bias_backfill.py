#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_station_confusion_edge import (  # noqa: E402
    build_station_bias_table,
    load_jsonl,
    scan_station_confusion_candidates,
    write_json,
    write_jsonl,
)
from src.weather.station_registry import station_registry_snapshot  # noqa: E402


DEFAULT_ROOT = Path("evidence/station_confusion")
DEFAULT_BIAS_OUTPUT = DEFAULT_ROOT / "station_bias_table.json"
DEFAULT_BACKFILL_OUTPUT = DEFAULT_ROOT / "station_bias_backfill_report.json"
DEFAULT_EDGE_OUTPUT = DEFAULT_ROOT / "station_confusion_edge_report.json"
DEFAULT_CANDIDATES_OUTPUT = DEFAULT_ROOT / "candidates.jsonl"
DEFAULT_STATION_OBSERVATIONS = Path("evidence/official_observations/intraday_observations.jsonl")
DEFAULT_CITY_GRID_OBSERVATIONS = Path("evidence/historical_forecasts/open_meteo_forecasts.jsonl")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _station_lookup() -> Dict[str, Dict[str, Any]]:
    return {
        str(spec.get("station_code") or "").strip().upper(): {"city": city, **spec}
        for city, spec in station_registry_snapshot().items()
        if isinstance(spec, dict) and str(spec.get("station_code") or "").strip()
    }


def _source_for_station(station_code: str) -> str:
    spec = _station_lookup().get(str(station_code or "").strip().upper()) or {}
    return str(spec.get("settlement_source") or "metar").strip().lower()


def _city_for_station(station_code: str) -> str:
    spec = _station_lookup().get(str(station_code or "").strip().upper()) or {}
    return str(spec.get("city") or "").strip().lower()


def _row_dict(row: Dict[str, Any], field: str) -> Dict[str, Any]:
    value = row.get(field)
    return value if isinstance(value, dict) else {}


def _first_text(row: Dict[str, Any], *fields: str) -> str:
    for source in (row, _row_dict(row, "settlement_spec"), _row_dict(row, "market_bucket")):
        for field in fields:
            text = str(source.get(field) or "").strip()
            if text:
                return text
    return ""


def _load_rows_json(path: Optional[str | Path]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def _temperature_value(row: Dict[str, Any], *fields: str) -> Optional[float]:
    for field in fields:
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for field in fields:
        value = _safe_float(payload.get(field))
        if value is not None:
            return value
    return None


def active_station_specs(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = _first_text(row, "station_code", "settlement_station_code").upper()
        source = _first_text(row, "settlement_source").lower() or _source_for_station(station)
        city = _first_text(row, "city").lower() or _city_for_station(station)
        if not station or not city:
            continue
        by_key[(city, station, source)] = {
            "city": city,
            "station_code": station,
            "settlement_source": source,
        }
    return [by_key[key] for key in sorted(by_key)]


def _station_final_high_rows(rows: Iterable[Dict[str, Any]], active_specs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    allowed = {(spec["station_code"], spec["settlement_source"]) for spec in active_specs}
    grouped: Dict[Tuple[str, str, str], float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = str(row.get("station_code") or "").strip().upper()
        source = str(row.get("settlement_source") or row.get("source") or _source_for_station(station)).strip().lower()
        if source.startswith("aviationweather_metar"):
            source = "metar"
        if allowed and (station, source) not in allowed:
            continue
        target_date = str(row.get("target_date_local") or row.get("target_date") or "").strip()
        temp = _temperature_value(row, "station_final_high", "official_final_value", "max_temp_c", "current_high_c", "temperature_c")
        if not station or not target_date or temp is None:
            continue
        key = (station, source, target_date)
        grouped[key] = max(float(temp), grouped.get(key, float("-inf")))
    normalized: List[Dict[str, Any]] = []
    for (station, source, target_date), value in sorted(grouped.items()):
        normalized.append(
            {
                "city": _city_for_station(station),
                "station_code": station,
                "settlement_source": source,
                "target_date": target_date,
                "station_final_high": value,
            }
        )
    return normalized


def _city_grid_high_rows(rows: Iterable[Dict[str, Any]], active_specs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    allowed_stations = {spec["station_code"] for spec in active_specs}
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = str(row.get("station_code") or "").strip().upper()
        if allowed_stations and station not in allowed_stations:
            continue
        target_date = str(row.get("target_date") or row.get("target_date_local") or "").strip()
        high = _temperature_value(row, "city_grid_final_high", "city_grid_final_high_c", "predicted_daily_high", "max_temp_c")
        if not station or not target_date or high is None:
            continue
        normalized.append(
            {
                "city": str(row.get("city") or _city_for_station(station)).strip().lower(),
                "station_code": station,
                "settlement_source": _source_for_station(station),
                "target_date": target_date,
                "city_grid_final_high": float(high),
            }
        )
    return normalized


def _pair_rows(active_specs: List[Dict[str, Any]], station_rows: List[Dict[str, Any]], city_rows: List[Dict[str, Any]], bias_table: Dict[str, Any]) -> List[Dict[str, Any]]:
    station_dates = defaultdict(set)
    city_dates = defaultdict(set)
    for row in station_rows:
        station_dates[(row["city"], row["station_code"], row["settlement_source"])].add(row["target_date"])
    for row in city_rows:
        city_dates[(row["city"], row["station_code"], row["settlement_source"])].add(row["target_date"])
    bias_by_key = {
        (row.get("city"), row.get("station_code"), row.get("settlement_source")): row
        for row in bias_table.get("station_biases") or []
        if isinstance(row, dict)
    }
    pairs: List[Dict[str, Any]] = []
    for spec in active_specs:
        key = (spec["city"], spec["station_code"], spec["settlement_source"])
        bias = bias_by_key.get(key) or {}
        station_count = len(station_dates.get(key) or set())
        city_count = len(city_dates.get(key) or set())
        paired = int(bias.get("sample_count") or 0)
        if paired <= 0:
            if station_count <= 0 and city_count <= 0:
                gap = "missing_station_and_city_grid_history"
            elif station_count <= 0:
                gap = "missing_station_history"
            elif city_count <= 0:
                gap = "missing_city_grid_history"
            else:
                gap = "station_city_dates_do_not_overlap"
        elif paired < 10:
            gap = "bias_sample_too_small"
        else:
            gap = None
        pairs.append(
            {
                "city": spec["city"],
                "station_code": spec["station_code"],
                "settlement_source": spec["settlement_source"],
                "sample_count": paired,
                "station_high_mean": bias.get("station_high_mean"),
                "city_grid_high_mean": bias.get("city_grid_high_mean"),
                "station_minus_city_bias_mean": bias.get("station_minus_city_bias_mean") or bias.get("historical_bias_mean"),
                "station_minus_city_bias_std": bias.get("station_minus_city_bias_std") or bias.get("historical_bias_std"),
                "bias_direction": bias.get("bias_direction"),
                "latest_sample_date": bias.get("latest_sample_date"),
                "source_coverage": {
                    "station_sample_count": station_count,
                    "city_grid_sample_count": city_count,
                    "paired_sample_count": paired,
                },
                "gap_reason": gap,
            }
        )
    return pairs


def build_station_confusion_bias_backfill_report(
    *,
    active_rows: List[Dict[str, Any]],
    station_observation_rows: List[Dict[str, Any]],
    city_grid_rows: List[Dict[str, Any]],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    active_specs = active_station_specs(active_rows)
    station_rows = _station_final_high_rows(station_observation_rows, active_specs)
    city_rows = _city_grid_high_rows(city_grid_rows, active_specs)
    bias_table = build_station_bias_table(
        station_rows=station_rows,
        city_grid_rows=city_rows,
        generated_at=generated_at,
    )
    pair_rows = _pair_rows(active_specs, station_rows, city_rows, bias_table)
    present = {(row.get("city"), row.get("station_code"), row.get("settlement_source")) for row in bias_table.get("station_biases") or [] if isinstance(row, dict)}
    for pair in pair_rows:
        key = (pair.get("city"), pair.get("station_code"), pair.get("settlement_source"))
        if key not in present:
            bias_table.setdefault("station_biases", []).append(pair)
    max_sample_count = max((int(row.get("sample_count") or 0) for row in pair_rows), default=0)
    if max_sample_count <= 0:
        status = "station_confusion_data_unavailable_reduce_priority"
    elif max_sample_count < 10:
        status = "station_confusion_bias_sample_too_small"
    else:
        status = "station_confusion_bias_ready_for_scan"
    return {
        "schema_version": "polyweather_station_confusion_bias_backfill.v1",
        "strategy_id": "station_confusion_edge",
        "platform": "polymarket",
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "active_station_count": len(active_specs),
        "active_station_specs": active_specs,
        "station_observation_normalized_count": len(station_rows),
        "city_grid_normalized_count": len(city_rows),
        "station_bias_sample_count": int(bias_table.get("station_bias_sample_count") or 0),
        "max_station_pair_sample_count": max_sample_count,
        "station_pairs": pair_rows,
        "station_bias_table": bias_table,
        "status": status,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build minimal station-city bias table for active Polymarket weather stations.")
    parser.add_argument("--rows-json", default=None)
    parser.add_argument("--station-observations", default=str(DEFAULT_STATION_OBSERVATIONS))
    parser.add_argument("--city-grid-observations", default=str(DEFAULT_CITY_GRID_OBSERVATIONS))
    parser.add_argument("--bias-output", default=str(DEFAULT_BIAS_OUTPUT))
    parser.add_argument("--backfill-output", default=str(DEFAULT_BACKFILL_OUTPUT))
    parser.add_argument("--edge-output", default=str(DEFAULT_EDGE_OUTPUT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES_OUTPUT))
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--min-bias-sample-count", type=int, default=10)
    parser.add_argument("--min-abs-confusion-edge", type=float, default=0.35)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    rows = _load_rows_json(args.rows_json)
    if rows is None:
        payload = build_polymarket_weather_payload(
            queries=("temperature",),
            row_limit=max(1, int(args.polymarket_row_limit)),
            include_order_books=True,
            include_city_temperature_queries=True,
            max_city_temperature_queries=8,
        )
        rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    backfill = build_station_confusion_bias_backfill_report(
        active_rows=rows,
        station_observation_rows=load_jsonl(args.station_observations),
        city_grid_rows=load_jsonl(args.city_grid_observations),
        generated_at=args.generated_at,
    )
    bias_table = backfill.get("station_bias_table") if isinstance(backfill.get("station_bias_table"), dict) else {}
    candidates, reason_counts = scan_station_confusion_candidates(
        rows,
        bias_table=bias_table,
        generated_at=args.generated_at,
        min_abs_confusion_edge=float(args.min_abs_confusion_edge),
        min_bias_sample_count=int(args.min_bias_sample_count),
        min_ask_depth=float(args.min_ask_depth),
    )
    edge_report = {
        "schema_version": "polyweather_station_confusion_edge.v1",
        "strategy_id": "station_confusion_edge",
        "platform": "polymarket",
        "generated_at": args.generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "station_bias_table": bias_table,
        "station_bias_sample_count": int(bias_table.get("station_bias_sample_count") or 0),
        "top_station_biases": (bias_table.get("station_biases") or [])[:10],
        "candidate_count": len(candidates),
        "active_candidate_count": len(candidates),
        "candidates": candidates,
        "no_candidate_reason_counts": reason_counts,
        "bias_backfill_status": backfill.get("status"),
    }
    if len(candidates) > 0:
        edge_report["alpha_conclusion"] = "station_confusion_forward_paper_candidate_found"
    elif int(backfill.get("max_station_pair_sample_count") or 0) < int(args.min_bias_sample_count):
        edge_report["alpha_conclusion"] = "station_confusion_data_unavailable_reduce_priority"
    else:
        edge_report["alpha_conclusion"] = "station_confusion_no_current_edge"
    write_json(args.bias_output, bias_table)
    write_json(args.backfill_output, {key: value for key, value in backfill.items() if key != "station_bias_table"})
    write_json(args.edge_output, {key: value for key, value in edge_report.items() if key not in {"candidates", "station_bias_table"}})
    write_jsonl(args.candidates_output, candidates)
    print(
        json.dumps(
            {
                "active_station_count": backfill.get("active_station_count"),
                "station_bias_sample_count": bias_table.get("station_bias_sample_count"),
                "max_station_pair_sample_count": backfill.get("max_station_pair_sample_count"),
                "candidate_count": len(candidates),
                "alpha_conclusion": edge_report.get("alpha_conclusion"),
                "live_order_path": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
