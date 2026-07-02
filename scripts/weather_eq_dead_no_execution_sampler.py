#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_orderbook_archive import build_orderbook_snapshot_records  # noqa: E402
from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_observation_lock_signal import build_observation_lock_signal_report  # noqa: E402
from src.trading.weather_paper_journal import _append_jsonl, stable_json_hash, utc_now_iso  # noqa: E402
from src.weather.station_registry import (  # noqa: E402
    SUPPORTED_OFFICIAL_SOURCE_ADAPTERS,
    active_supported_metar_station_manifest,
)


SCHEMA_VERSION = "polyweather_eq_dead_no_execution_sampler.v1"
DEFAULT_ROOT = Path("evidence/eq_dead_no")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def _load_rows_json(path: Optional[str | Path]) -> Optional[list[dict]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def _row_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _field_text(row: Dict[str, Any], field: str) -> str:
    spec = _row_dict(row.get("settlement_spec"))
    bucket = _row_dict(row.get("market_bucket"))
    for source in (row, spec, bucket):
        text = _text(source.get(field))
        if text:
            return text
    return ""


def _is_temperature_row(row: Dict[str, Any]) -> bool:
    family = _field_text(row, "market_family").lower()
    if family == "temperature":
        return True
    metric = _field_text(row, "metric").lower()
    question = _text(row.get("question")).lower()
    return "temperature" in question or "temperature" in metric


def _market_closed(row: Dict[str, Any], generated_at: str) -> bool:
    close_time = _field_text(row, "market_close_time") or _field_text(row, "end_time") or _field_text(row, "end_date")
    close_dt = _parse_utc(close_time)
    generated_dt = _parse_utc(generated_at)
    return bool(close_dt is not None and generated_dt is not None and generated_dt >= close_dt)


def _station_code(row: Dict[str, Any]) -> str:
    return (_field_text(row, "station_code") or _field_text(row, "settlement_station_code")).upper()


def _settlement_source(row: Dict[str, Any]) -> str:
    return _field_text(row, "settlement_source").lower()


def _source_supported(row: Dict[str, Any]) -> bool:
    return _settlement_source(row) in SUPPORTED_OFFICIAL_SOURCE_ADAPTERS


def _counter_rows(counter: Counter[str], field: str) -> list[dict]:
    return [{field: key, "count": value} for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]


def _price_band(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "price_unknown"
    if price <= 0.80:
        return "price_le_0_80"
    if price <= 0.95:
        return "price_0_80_to_0_95"
    return "price_gt_0_95_watch_only"


def _collect_intraday(args: argparse.Namespace, *, station_codes: list[str]) -> Dict[str, Any]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "weather_collect_intraday_observations.py"),
        "--output",
        args.intraday_observation_path,
        "--manifest",
        args.intraday_manifest_path,
    ]
    for station in station_codes:
        command.extend(["--station-code", station])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "station_codes": station_codes,
    }


def _paper_fill(row: Dict[str, Any], *, recorded_at: str, snapshot_id: Optional[str], cost: float) -> Dict[str, Any]:
    identity = {
        "strategy_id": "eq_dead_no_lock",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("locked_side_token_id") or row.get("token_id"),
        "latest_available_at": row.get("latest_available_at"),
    }
    entry = _safe_float(row.get("q_effective"))
    return {
        "schema_version": "polyweather_eq_dead_no_paper_fill.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_id": stable_json_hash(identity, length=24),
        "strategy_id": "eq_dead_no_lock",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("locked_side_token_id") or row.get("token_id"),
        "threshold": row.get("threshold"),
        "official_current_high": row.get("official_current_high"),
        "latest_observation_at": row.get("latest_observation_at"),
        "latest_available_at": row.get("latest_available_at"),
        "no_best_ask": row.get("locked_side_best_ask"),
        "no_ask_depth": row.get("locked_side_ask_depth"),
        "entry_price": entry,
        "q_effective": entry,
        "executable_edge": round(1.0 - float(entry) - float(cost), 6) if entry is not None else None,
        "price_bucket": row.get("price_bucket"),
        "entry_price_band": _price_band(entry),
        "orderbook_snapshot_id": snapshot_id,
        "station_code": row.get("station_code"),
        "target_date": row.get("target_date"),
        "settlement_spec": row.get("settlement_spec"),
        "no_lookahead": row.get("no_lookahead", True),
    }


def _collection_manifest(
    *,
    source_rows: list[Dict[str, Any]],
    intraday_rows: list[Dict[str, Any]],
    requested_station_codes: list[str],
) -> Dict[str, Any]:
    manifest = active_supported_metar_station_manifest(source_rows)
    collected = {
        _text(row.get("station_code")).upper()
        for row in intraday_rows
        if _text(row.get("station_code")) and _text(row.get("source") or row.get("settlement_source")).lower().startswith(("metar", "aviationweather_metar"))
    }
    expected = set(manifest.get("supported_official_station_codes") or manifest.get("station_codes") or requested_station_codes)
    manifest["requested_station_codes"] = sorted(set(requested_station_codes))
    manifest["collected_station_count"] = len(collected & expected) if expected else len(collected)
    manifest["collection_gap_by_station"] = [
        {"station_code": station, "gap_reason": "missing_intraday_observation_after_collection"}
        for station in sorted(expected)
        if station not in collected
    ]
    return manifest


def _build_opportunity_funnel(
    *,
    source_rows: list[Dict[str, Any]],
    signal_rows: list[Dict[str, Any]],
    candidates: list[Dict[str, Any]],
    paper_fill_count: int,
    generated_at: str,
    min_ask_depth: float,
    max_entry_price: float,
) -> Dict[str, Any]:
    signal_by_slug_token = {
        (_text(row.get("market_slug")), _text(row.get("token_id"))): row
        for row in signal_rows
    }
    temperature_source = [row for row in source_rows if _is_temperature_row(row)]
    exact_source = [row for row in temperature_source if _field_text(row, "bucket_type").lower() == "eq"]
    supported_source = [row for row in exact_source if _source_supported(row)]
    intraday_rows = [row for row in signal_rows if row.get("bucket_type") == "eq" and int(row.get("intraday_observation_count") or 0) > 0]
    breached = [row for row in signal_rows if row.get("bucket_type") == "eq" and row.get("lock_state") == "eq_yes_dead_no_locked"]
    direct_no = [row for row in breached if row.get("executable_price_source_type") == "direct_locked_side_book"]
    no_ask_available = [row for row in direct_no if _safe_float(row.get("locked_side_best_ask")) is not None]
    ask_depth_sufficient = [
        row
        for row in no_ask_available
        if _safe_float(row.get("locked_side_ask_depth")) is not None
        and float(row.get("locked_side_ask_depth") or 0.0) >= float(min_ask_depth)
    ]
    non_dust = [row for row in ask_depth_sufficient if row.get("price_bucket") != "price_lt_0_005"]
    price_le_max = [
        row
        for row in non_dust
        if _safe_float(row.get("q_effective")) is not None and float(row.get("q_effective") or 0.0) <= float(max_entry_price)
    ]
    blocker_counts: Counter[str] = Counter()
    for row in source_rows:
        if not _is_temperature_row(row):
            blocker_counts["not_temperature"] += 1
            continue
        if _field_text(row, "bucket_type").lower() != "eq":
            blocker_counts["not_eq"] += 1
            continue
        if not _source_supported(row):
            blocker_counts["unsupported_source"] += 1
        if _market_closed(row, generated_at):
            blocker_counts["market_closed"] += 1
        signal = signal_by_slug_token.get((_text(row.get("market_slug")), _text(row.get("token_id"))))
        if not signal:
            continue
        if int(signal.get("intraday_observation_count") or 0) <= 0:
            blocker_counts["missing_intraday"] += 1
        if signal.get("lock_state") != "eq_yes_dead_no_locked":
            blocker_counts["not_breached"] += 1
        if not _text(signal.get("locked_side_token_id")):
            blocker_counts["missing_no_token"] += 1
        if signal.get("executable_price_source_type") != "direct_locked_side_book":
            blocker_counts["missing_no_book"] += 1
        if _safe_float(signal.get("locked_side_best_ask")) is None:
            blocker_counts["missing_no_ask"] += 1
        if _safe_float(signal.get("locked_side_ask_depth")) is None or float(signal.get("locked_side_ask_depth") or 0.0) < float(min_ask_depth):
            blocker_counts["ask_depth_too_low"] += 1
        if signal.get("price_bucket") == "price_lt_0_005":
            blocker_counts["dust_price"] += 1
        if _safe_float(signal.get("q_effective")) is not None and float(signal.get("q_effective") or 0.0) > float(max_entry_price):
            blocker_counts["price_too_high"] += 1
        if signal.get("anomaly_flags"):
            blocker_counts["anomaly"] += 1
    return {
        "schema_version": "polyweather_eq_dead_no_opportunity_funnel.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "total_active_rows": len(source_rows),
        "temperature_rows": len(temperature_source),
        "exact_bucket_rows": len(exact_source),
        "supported_source_rows": len(supported_source),
        "intraday_available_rows": len(intraday_rows),
        "breached_eq_rows": len(breached),
        "direct_no_book_rows": len(direct_no),
        "no_ask_available_rows": len(no_ask_available),
        "ask_depth_sufficient_rows": len(ask_depth_sufficient),
        "non_dust_rows": len(non_dust),
        "price_le_0_95_rows": len(price_le_max),
        "executable_candidate_rows": len(candidates),
        "paper_fill_rows": int(paper_fill_count),
        "blocker_counts": _counter_rows(blocker_counts, "blocker"),
    }


def _build_nearest_breach_watchlist(
    *,
    signal_rows: list[Dict[str, Any]],
    generated_at: str,
    limit: int = 25,
) -> Dict[str, Any]:
    watch_rows: list[Dict[str, Any]] = []
    for row in signal_rows:
        if row.get("bucket_type") != "eq":
            continue
        threshold = _safe_float(row.get("threshold"))
        current_high = _safe_float(row.get("official_current_high"))
        if threshold is None:
            continue
        distance = round(float(threshold) - float(current_high), 6) if current_high is not None else None
        blocker = "missing_intraday" if current_high is None else "not_breached"
        if row.get("settlement_source") not in SUPPORTED_OFFICIAL_SOURCE_ADAPTERS:
            blocker = "unsupported_source"
        elif row.get("executable_price_source_type") != "direct_locked_side_book":
            blocker = "missing_no_book"
        elif _safe_float(row.get("locked_side_best_ask")) is None:
            blocker = "missing_no_ask"
        elif distance is not None and distance <= 0:
            blocker = "already_breached"
        watch_rows.append(
            {
                "market_slug": row.get("market_slug"),
                "station_code": row.get("station_code"),
                "threshold": threshold,
                "official_current_high": current_high,
                "distance_to_breach": distance,
                "no_token_id": row.get("locked_side_token_id"),
                "no_best_ask": row.get("locked_side_best_ask"),
                "no_ask_depth": row.get("locked_side_ask_depth"),
                "market_close_time": row.get("market_close_time"),
                "observation_window_end_time": row.get("observation_window_end_time"),
                "next_expected_check": generated_at,
                "blocker": blocker,
                "supported_source": row.get("settlement_source") in SUPPORTED_OFFICIAL_SOURCE_ADAPTERS,
                "non_dust": row.get("price_bucket") != "price_lt_0_005",
                "direct_no_book_available": row.get("executable_price_source_type") == "direct_locked_side_book",
            }
        )
    def sort_key(row: Dict[str, Any]) -> tuple[int, int, int, float]:
        distance = row.get("distance_to_breach")
        if distance is None or float(distance) < 0:
            distance_value = 999999.0
        else:
            distance_value = float(distance)
        return (
            0 if row.get("supported_source") else 1,
            0 if row.get("non_dust") else 1,
            0 if row.get("direct_no_book_available") else 1,
            distance_value,
        )
    watch_rows = sorted(watch_rows, key=sort_key)[: max(0, int(limit))]
    return {
        "schema_version": "polyweather_eq_dead_no_nearest_breach_watchlist.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "watchlist_count": len(watch_rows),
        "rows": watch_rows,
    }


def build_eq_dead_no_execution_sampler_report(
    *,
    rows: Iterable[Dict[str, Any]],
    intraday_observation_path: str | Path,
    orderbook_archive_dir: str | Path = DEFAULT_ROOT,
    paper_fill_dir: str | Path = DEFAULT_ROOT,
    signal_report_output: Optional[str | Path] = None,
    active_station_manifest_output: Optional[str | Path] = None,
    opportunity_funnel_output: Optional[str | Path] = None,
    nearest_breach_watchlist_output: Optional[str | Path] = None,
    generated_at: Optional[str] = None,
    max_candidates: int = 20,
    max_entry_price: float = 0.95,
    min_ask_depth: float = 1.0,
    max_spread: float = 0.03,
    exclude_dust: bool = True,
    default_cost: float = 0.005,
    requested_station_codes: Optional[list[str]] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    source_rows = [row for row in rows if isinstance(row, dict)]
    intraday_rows = _load_intraday_rows(intraday_observation_path)
    station_manifest = _collection_manifest(
        source_rows=source_rows,
        intraday_rows=intraday_rows,
        requested_station_codes=requested_station_codes or [],
    )
    if active_station_manifest_output:
        _write_json(active_station_manifest_output, station_manifest)
    signal_report = build_observation_lock_signal_report(
        source_rows,
        intraday_repository=None,
        observations=intraday_rows,
        generated_at=generated_at,
        max_spread=max_spread,
        min_ask_depth=min_ask_depth,
        min_executable_edge=0.0,
        default_cost=default_cost,
    )
    if signal_report_output:
        _write_json(signal_report_output, signal_report)
    signal_rows = [row for row in signal_report.get("rows") or [] if isinstance(row, dict)]
    eq_rows = [row for row in signal_rows if row.get("bucket_type") == "eq"]
    breached = [row for row in eq_rows if row.get("lock_state") == "eq_yes_dead_no_locked"]
    direct_no = [row for row in breached if row.get("executable_price_source_type") == "direct_locked_side_book"]
    candidates: list[Dict[str, Any]] = []
    watch_rows: list[Dict[str, Any]] = []
    reject_counter: Counter[str] = Counter()
    for row in breached:
        blockers = list(row.get("blockers") or [])
        q_effective = _safe_float(row.get("q_effective"))
        if row.get("token_side") == "YES":
            blockers.append("eq_yes_side_rejected")
        if row.get("executable_price_source_type") != "direct_locked_side_book":
            blockers.append("missing_direct_no_book")
        if q_effective is None:
            blockers.append("missing_no_best_ask")
        elif q_effective > float(max_entry_price):
            blockers.append("entry_price_above_max")
        if exclude_dust and row.get("price_bucket") == "price_lt_0_005":
            blockers.append("dust_price_rejected")
        if row.get("decision") == "candidate" and not blockers:
            candidates.append(row)
        else:
            watch = {**row, "eq_dead_no_sampler_blockers": sorted(set(blockers))}
            watch_rows.append(watch)
            for blocker in blockers:
                reject_counter[str(blocker)] += 1
    candidates = candidates[: max(0, int(max_candidates))]
    by_token = {_text(row.get("token_id")): row for row in source_rows if _text(row.get("token_id"))}
    candidate_source_rows = [by_token.get(_text(row.get("locked_side_token_id") or row.get("token_id"))) for row in candidates]
    candidate_source_rows = [row for row in candidate_source_rows if isinstance(row, dict)]
    archive_root = Path(orderbook_archive_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    snapshot_records = build_orderbook_snapshot_records(
        candidate_source_rows,
        recorded_at=generated_at,
        source_snapshot_id=f"eq-dead-no-{generated_at}",
        source="eq_dead_no_execution_sampler",
    )
    snapshot_path = archive_root / "orderbook_snapshots.jsonl"
    snapshot_path.touch(exist_ok=True)
    snapshot_written = _append_jsonl(snapshot_path, snapshot_records)
    snapshot_by_token = {_text(row.get("token_id")): row for row in snapshot_records}
    fills = [
        _paper_fill(
            row,
            recorded_at=generated_at,
            snapshot_id=(snapshot_by_token.get(_text(row.get("locked_side_token_id") or row.get("token_id"))) or {}).get("snapshot_id"),
            cost=default_cost,
        )
        for row in candidates
    ]
    fill_root = Path(paper_fill_dir)
    fill_root.mkdir(parents=True, exist_ok=True)
    fill_path = fill_root / "paper_fills.jsonl"
    fill_path.touch(exist_ok=True)
    fills_written = _append_jsonl(fill_path, fills)
    watch_path = archive_root / "watch_rows.jsonl"
    watch_written = _write_jsonl(watch_path, watch_rows)
    opportunity_funnel = _build_opportunity_funnel(
        source_rows=source_rows,
        signal_rows=signal_rows,
        candidates=candidates,
        paper_fill_count=len(fills),
        generated_at=generated_at,
        min_ask_depth=min_ask_depth,
        max_entry_price=max_entry_price,
    )
    nearest_breach_watchlist = _build_nearest_breach_watchlist(signal_rows=signal_rows, generated_at=generated_at)
    if opportunity_funnel_output:
        _write_json(opportunity_funnel_output, opportunity_funnel)
    if nearest_breach_watchlist_output:
        _write_json(nearest_breach_watchlist_output, nearest_breach_watchlist)
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "scanned_eq_rows": len(eq_rows),
        "active_supported_metar_station_count": station_manifest.get("active_supported_metar_station_count"),
        "active_supported_official_station_count": station_manifest.get("active_supported_official_station_count"),
        "active_eq_row_count": station_manifest.get("active_eq_row_count"),
        "station_codes": station_manifest.get("station_codes") or [],
        "supported_official_station_codes": station_manifest.get("supported_official_station_codes") or [],
        "breached_eq_count": len(breached),
        "direct_no_book_count": len(direct_no),
        "no_ask_available_count": len([row for row in direct_no if row.get("locked_side_best_ask") is not None]),
        "executable_candidate_count": len(candidates),
        "paper_fill_count": len(fills),
        "paper_fill_written_count": fills_written,
        "watch_count": len(watch_rows),
        "watch_rows_written_count": watch_written,
        "orderbook_snapshot_count": len(snapshot_records),
        "orderbook_snapshot_written_count": snapshot_written,
        "reject_reason_counts": _counter_rows(reject_counter, "reason"),
        "candidate_samples": candidates[:10],
        "top_watch_samples": watch_rows[:10],
        "price_band_counts": _counter_rows(Counter(_price_band(row.get("q_effective")) for row in breached), "entry_price_band"),
        "active_station_scan_manifest": station_manifest,
        "opportunity_funnel": opportunity_funnel,
        "nearest_breach_watchlist": {
            key: value for key, value in nearest_breach_watchlist.items() if key != "rows"
        },
        "paths": {
            "signal_report": str(signal_report_output) if signal_report_output else None,
            "active_station_scan_manifest": str(active_station_manifest_output) if active_station_manifest_output else None,
            "opportunity_funnel": str(opportunity_funnel_output) if opportunity_funnel_output else None,
            "nearest_breach_watchlist": str(nearest_breach_watchlist_output) if nearest_breach_watchlist_output else None,
            "orderbook_snapshots": str(snapshot_path),
            "paper_fills": str(fill_path),
            "watch_rows": str(watch_path),
        },
        "max_entry_price": float(max_entry_price),
        "exclude_dust": bool(exclude_dust),
    }
    _write_json(archive_root / "eq_dead_no_execution_sampler_report.json", report)
    return report


def _load_intraday_rows(path: str | Path) -> list[dict]:
    source = Path(path)
    if not source.exists():
        return []
    rows: list[dict] = []
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


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only eq-dead-NO execution sampler.")
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--rows-json", default=None)
    parser.add_argument("--intraday-observation-path", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--intraday-manifest-path", default="evidence/official_observations/manifest.json")
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--paper-fill-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--signal-report-output", default=str(DEFAULT_ROOT / "eq_dead_no_signal_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "eq_dead_no_execution_sampler_report.json"))
    parser.add_argument("--active-station-manifest-output", default=str(DEFAULT_ROOT / "active_eq_station_scan_manifest.json"))
    parser.add_argument("--opportunity-funnel-output", default=str(DEFAULT_ROOT / "eq_dead_no_opportunity_funnel.json"))
    parser.add_argument("--nearest-breach-watchlist-output", default=str(DEFAULT_ROOT / "eq_dead_no_nearest_breach_watchlist.json"))
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--max-entry-price", type=float, default=0.95)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--exclude-dust", action="store_true")
    parser.add_argument("--collect-intraday-before-scan", action="store_true")
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
    parser.add_argument("--auto-active-supported-stations", action="store_true")
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
            max_city_temperature_queries=6,
        )
        rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    station_manifest = active_supported_metar_station_manifest(rows)
    default_station_codes = ["LTAC", "UUWW", "EGLC"]
    fallback_station_codes = [str(code).strip().upper() for code in (args.station_codes or default_station_codes) if str(code).strip()]
    requested_station_codes = (
        [
            str(code).strip().upper()
            for code in (
                station_manifest.get("supported_official_station_codes")
                or station_manifest.get("station_codes")
                or []
            )
            if str(code).strip()
        ]
        if args.auto_active_supported_stations
        else []
    )
    if not requested_station_codes:
        requested_station_codes = fallback_station_codes
    if args.collect_intraday_before_scan:
        collection = _collect_intraday(args, station_codes=requested_station_codes)
        if collection["returncode"] != 0:
            report = {
                "schema_version": SCHEMA_VERSION,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "collector_failed": True,
                "collector_report": collection,
                "auto_active_supported_stations": bool(args.auto_active_supported_stations),
                "active_station_scan_manifest": station_manifest,
                "requested_station_codes": requested_station_codes,
                "executable_candidate_count": 0,
                "paper_fill_count": 0,
            }
            _write_json(args.summary_output, report)
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return
    report = build_eq_dead_no_execution_sampler_report(
        rows=rows,
        intraday_observation_path=args.intraday_observation_path,
        orderbook_archive_dir=args.orderbook_archive_dir,
        paper_fill_dir=args.paper_fill_dir,
        signal_report_output=args.signal_report_output,
        active_station_manifest_output=args.active_station_manifest_output,
        opportunity_funnel_output=args.opportunity_funnel_output,
        nearest_breach_watchlist_output=args.nearest_breach_watchlist_output,
        max_candidates=args.max_candidates,
        max_entry_price=args.max_entry_price,
        min_ask_depth=args.min_ask_depth,
        max_spread=args.max_spread,
        exclude_dust=args.exclude_dust,
        requested_station_codes=requested_station_codes,
    )
    report["auto_active_supported_stations"] = bool(args.auto_active_supported_stations)
    report["explicit_station_codes_fallback"] = fallback_station_codes
    report["requested_station_codes"] = requested_station_codes
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
