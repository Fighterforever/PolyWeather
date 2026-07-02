#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_orderbook_archive import build_orderbook_snapshot_records  # noqa: E402
from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_observation_lock_signal import build_observation_lock_signal_report  # noqa: E402
from src.trading.weather_paper_journal import _append_jsonl, stable_json_hash, utc_now_iso  # noqa: E402
from src.weather.weather_observations import OfficialIntradayObservationRepository  # noqa: E402
from src.weather.weather_sources import parse_utc  # noqa: E402


SCHEMA_VERSION = "polyweather_observation_lock_execution_sampler.v1"
DEFAULT_ROOT = Path("evidence/observation_lock_execution")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_rows_json(path: Optional[str | Path]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def _is_fresh(row: Dict[str, Any], *, generated_at: str, max_staleness_minutes: float) -> bool:
    latest = parse_utc(row.get("latest_available_at"))
    now = parse_utc(generated_at)
    if latest is None or now is None:
        return False
    return (now - latest).total_seconds() <= float(max_staleness_minutes) * 60.0


def _candidate_reject_reasons(
    row: Dict[str, Any],
    *,
    generated_at: str,
    bucket_types: set[str],
    exclude_dust: bool,
    max_staleness_minutes: float,
) -> List[str]:
    reasons = list(row.get("blockers") or [])
    bucket = _text(row.get("bucket_type")).lower()
    if bucket not in bucket_types:
        reasons.append("bucket_type_not_sampled")
    if exclude_dust and row.get("price_bucket") == "price_lt_0_005":
        reasons.append("dust_price")
    if row.get("lock_state") not in {"ge_yes_locked", "le_yes_dead_no_locked"}:
        reasons.append("not_observation_locked")
    if row.get("official_current_high") is None:
        reasons.append("missing_intraday_observation")
    if row.get("freshness_blocker"):
        reasons.append(str(row.get("freshness_blocker")))
    elif (
        not row.get("lock_is_immutable")
        and row.get("latest_available_at")
        and not _is_fresh(row, generated_at=generated_at, max_staleness_minutes=max_staleness_minutes)
    ):
        reasons.append("stale_intraday_observation")
    if row.get("latest_available_at") is None:
        reasons.append("missing_intraday_available_at")
    if row.get("anomaly_flags"):
        reasons.append("blocked_by_observation_anomaly")
    if row.get("executable_price_source_type") == "synthetic_from_opposite_bid":
        reasons.append("synthetic_no_price_diagnostic_only")
    if row.get("executable_price_source_type") != "direct_locked_side_book":
        reasons.append("missing_locked_side_orderbook")
    if not row.get("current_row_is_locked_side_token"):
        reasons.append("not_locked_side_token_row")
    if _safe_float(row.get("locked_side_ask_depth")) is None:
        reasons.append("missing_locked_side_ask_depth")
    elif float(row.get("locked_side_ask_depth") or 0.0) <= 0:
        reasons.append("locked_side_ask_depth_too_low")
    if row.get("executable_price_source_type") == "direct_locked_side_book" and (
        _safe_float(row.get("executable_edge")) is None or float(row.get("executable_edge") or 0.0) <= 0
    ):
        reasons.append("executable_edge_not_positive")
    return sorted(set(reasons))


def _fill_from_signal(row: Dict[str, Any], *, recorded_at: str) -> Dict[str, Any]:
    identity = {
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "locked_side": row.get("locked_side"),
        "latest_available_at": row.get("latest_available_at"),
    }
    return {
        "schema_version": "polyweather_observation_lock_execution_paper_fill.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_id": stable_json_hash(identity, length=24),
        "strategy_id": "observation_lock_execution_sampler",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "locked_side_token_id": row.get("locked_side_token_id"),
        "side": row.get("side"),
        "locked_side": row.get("locked_side"),
        "lock_state": row.get("lock_state"),
        "bucket_type": row.get("bucket_type"),
        "station_code": row.get("station_code"),
        "target_date": row.get("target_date"),
        "official_current_high": row.get("official_current_high"),
        "latest_observation_at": row.get("latest_observation_at"),
        "latest_available_at": row.get("latest_available_at"),
        "entry_price": row.get("q_effective"),
        "q_effective": row.get("q_effective"),
        "executable_edge": row.get("executable_edge"),
        "executable_price_source_type": row.get("executable_price_source_type"),
        "locked_side_ask_depth": row.get("locked_side_ask_depth"),
        "price_bucket": row.get("price_bucket"),
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id"),
        "settlement_spec": row.get("settlement_spec"),
        "no_lookahead": bool(row.get("no_lookahead") is not False),
    }


def _diagnostic_dead_side_fill_from_signal(row: Dict[str, Any], *, recorded_at: str) -> Dict[str, Any]:
    identity = {
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "dead_side_token_id": row.get("dead_side_token_id"),
        "execution_mode": "dead_side_bid_capture",
        "latest_available_at": row.get("latest_available_at"),
    }
    return {
        "schema_version": "polyweather_observation_lock_dead_side_capture_diagnostic_fill.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "live_gate_excluded": True,
        "live_order_path": False,
        "diagnostic_fill_id": stable_json_hash(identity, length=24),
        "strategy_id": "observation_lock_dead_side_bid_capture_diagnostic",
        "execution_mode": "dead_side_bid_capture",
        "execution_mode_status": "diagnostic_only_until_ctf_split_merge_supported",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "dead_side": row.get("dead_side"),
        "dead_side_token_id": row.get("dead_side_token_id"),
        "dead_side_best_bid": row.get("dead_side_best_bid"),
        "dead_side_bid_depth": row.get("dead_side_bid_depth"),
        "dead_side_capture_edge": row.get("dead_side_capture_edge"),
        "locked_side": row.get("locked_side"),
        "locked_side_token_id": row.get("locked_side_token_id"),
        "locked_side_best_bid": row.get("locked_side_best_bid"),
        "locked_side_best_ask": row.get("locked_side_best_ask"),
        "lock_state": row.get("lock_state"),
        "market_reflection_state": row.get("market_reflection_state"),
        "bucket_type": row.get("bucket_type"),
        "station_code": row.get("station_code"),
        "target_date": row.get("target_date"),
        "official_current_high": row.get("official_current_high"),
        "latest_observation_at": row.get("latest_observation_at"),
        "latest_available_at": row.get("latest_available_at"),
        "settlement_spec": row.get("settlement_spec"),
        "no_lookahead": bool(row.get("no_lookahead") is not False),
    }


def _write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    materialized = [row for row in rows if isinstance(row, dict)]
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(materialized)


def _counter_rows(counter: Counter[str], field: str) -> List[Dict[str, Any]]:
    return [
        {field: key, "count": count}
        for key, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _collect_intraday(args: argparse.Namespace) -> Dict[str, Any]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "weather_collect_intraday_observations.py"),
        "--output",
        args.intraday_observation_path,
        "--manifest",
        args.intraday_manifest_path,
    ]
    for station in args.station_codes or ["LTAC", "UUWW", "EGLC"]:
        command.extend(["--station-code", station])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "station_codes": args.station_codes or ["LTAC", "UUWW", "EGLC"],
        "observation_output_path": args.intraday_observation_path,
        "manifest_path": args.intraday_manifest_path,
    }


def _collector_failed_report(*, generated_at: str, collection_report: Dict[str, Any], summary_output: str | Path) -> Dict[str, Any]:
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "collector_failed": True,
        "collector_report": collection_report,
        "scanned_row_count": 0,
        "locked_count": 0,
        "ge_le_locked_count": 0,
        "executable_candidate_count": 0,
        "paper_fill_count": 0,
        "reject_reason_counts": [{"reason": "collector_failed", "count": 1}],
    }
    _write_json(summary_output, report)
    return report


def build_observation_lock_execution_sampler_report(
    *,
    rows: Iterable[Dict[str, Any]],
    intraday_observation_path: str | Path,
    orderbook_archive_dir: str | Path = DEFAULT_ROOT,
    paper_fill_dir: str | Path = DEFAULT_ROOT,
    signal_report_output: Optional[str | Path] = None,
    generated_at: Optional[str] = None,
    max_candidates: int = 20,
    bucket_types: Iterable[str] = ("ge", "le"),
    exclude_dust: bool = True,
    max_staleness_minutes: float = 10.0,
    min_executable_edge: float = 0.0,
    max_spread: float = 0.03,
    min_ask_depth: float = 1.0,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    source_rows = [row for row in rows if isinstance(row, dict)]
    repo = OfficialIntradayObservationRepository(intraday_observation_path)
    signal_report = build_observation_lock_signal_report(
        source_rows,
        intraday_repository=repo,
        generated_at=generated_at,
        min_executable_edge=float(min_executable_edge),
        max_spread=float(max_spread),
        min_ask_depth=float(min_ask_depth),
    )
    if signal_report_output:
        _write_json(signal_report_output, signal_report)
    sampled_buckets = {str(item).strip().lower() for item in bucket_types if str(item).strip()}
    by_token = {_text(row.get("token_id")): row for row in source_rows if _text(row.get("token_id"))}
    reject_counts: Counter[str] = Counter()
    ge_le_locked_reject_counts: Counter[str] = Counter()
    locked_watch_reason_counts: Counter[str] = Counter()
    candidates: List[Dict[str, Any]] = []
    watch_samples: List[Dict[str, Any]] = []
    locked_watch_rows: List[Dict[str, Any]] = []
    dead_side_watch_rows: List[Dict[str, Any]] = []
    for row in signal_report.get("rows") or []:
        if not isinstance(row, dict):
            continue
        reasons = _candidate_reject_reasons(
            row,
            generated_at=generated_at,
            bucket_types=sampled_buckets,
            exclude_dust=bool(exclude_dust),
            max_staleness_minutes=float(max_staleness_minutes),
        )
        if row.get("decision") == "candidate" and not reasons:
            candidates.append(row)
        else:
            for reason in reasons or ["not_candidate"]:
                reject_counts[reason] += 1
        is_ge_le_locked = row.get("lock_state") in {"ge_yes_locked", "le_yes_dead_no_locked"} and row.get("bucket_type") in sampled_buckets
        if is_ge_le_locked:
            for reason in reasons or ["candidate"]:
                ge_le_locked_reject_counts[reason] += 1
            dead_side_watch_rows.append(row)
            if row.get("decision") != "shadow":
                locked_watch = {**row, "execution_sampler_reject_reasons": reasons}
                locked_watch_rows.append(locked_watch)
                for reason in reasons or ["candidate"]:
                    locked_watch_reason_counts[reason] += 1
                if len(watch_samples) < 20:
                    watch_samples.append(locked_watch)
    candidates = candidates[: max(0, int(max_candidates))]
    candidate_source_rows = [by_token.get(_text(row.get("locked_side_token_id") or row.get("token_id"))) for row in candidates]
    candidate_source_rows = [row for row in candidate_source_rows if isinstance(row, dict)]
    snapshot_records = build_orderbook_snapshot_records(
        candidate_source_rows,
        recorded_at=generated_at,
        source_snapshot_id=f"observation-lock-execution-{generated_at}",
        source="observation_lock_execution_sampler",
    )
    snapshot_by_token = {_text(row.get("token_id")): row for row in snapshot_records}
    for candidate in candidates:
        snapshot = snapshot_by_token.get(_text(candidate.get("token_id")))
        if snapshot:
            candidate["orderbook_snapshot_id"] = snapshot.get("snapshot_id")
    archive_root = Path(orderbook_archive_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = archive_root / "orderbook_snapshots.jsonl"
    snapshot_path.touch(exist_ok=True)
    snapshots_written = _append_jsonl(snapshot_path, snapshot_records)
    fills = [_fill_from_signal(row, recorded_at=generated_at) for row in candidates]
    fill_root = Path(paper_fill_dir)
    fill_root.mkdir(parents=True, exist_ok=True)
    fill_path = fill_root / "paper_fills.jsonl"
    fill_path.touch(exist_ok=True)
    fills_written = _append_jsonl(fill_path, fills)
    locked_watch_source_rows = []
    seen_locked_watch_tokens: set[str] = set()
    for row in locked_watch_rows:
        token = _text(row.get("locked_side_token_id") or row.get("token_id"))
        if not token or token in seen_locked_watch_tokens:
            continue
        source_row = by_token.get(token)
        if isinstance(source_row, dict):
            locked_watch_source_rows.append(source_row)
            seen_locked_watch_tokens.add(token)
    locked_watch_snapshot_records = build_orderbook_snapshot_records(
        locked_watch_source_rows,
        recorded_at=generated_at,
        source_snapshot_id=f"observation-lock-locked-watch-{generated_at}",
        source="observation_lock_execution_sampler_locked_watch",
    )
    locked_watch_snapshot_path = archive_root / "locked_watch_orderbook_snapshots.jsonl"
    locked_watch_snapshot_path.touch(exist_ok=True)
    locked_watch_snapshots_written = _append_jsonl(locked_watch_snapshot_path, locked_watch_snapshot_records)
    locked_watch_rows_path = archive_root / "locked_watch_rows.jsonl"
    locked_watch_rows_written = _write_jsonl(locked_watch_rows_path, locked_watch_rows)
    dead_side_watch_rows_path = archive_root / "dead_side_capture_watch_rows.jsonl"
    dead_side_watch_rows_written = _write_jsonl(dead_side_watch_rows_path, dead_side_watch_rows)
    dead_side_diagnostic_fills = [
        _diagnostic_dead_side_fill_from_signal(row, recorded_at=generated_at)
        for row in dead_side_watch_rows
        if row.get("dead_side_capture_candidate") is True
    ]
    dead_side_diagnostic_fills_path = archive_root / "dead_side_capture_diagnostic_fills.jsonl"
    dead_side_diagnostic_fill_count = _write_jsonl(dead_side_diagnostic_fills_path, dead_side_diagnostic_fills)
    dead_side_blockers: Counter[str] = Counter()
    for row in dead_side_watch_rows:
        for blocker in row.get("dead_side_capture_blockers") or []:
            dead_side_blockers[str(blocker)] += 1
    dead_side_total_bid_depth = round(
        sum(float(row.get("dead_side_bid_depth") or 0.0) for row in dead_side_watch_rows),
        8,
    )
    summary = signal_report.get("summary") if isinstance(signal_report.get("summary"), dict) else {}
    dead_side_capture_report = {
        "schema_version": "polyweather_observation_lock_dead_side_capture_sampler.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "live_gate_excluded": True,
        "live_order_path": False,
        "generated_at": generated_at,
        "ge_le_locked_count": summary.get("ge_le_locked_count"),
        "direct_locked_side_taker_candidate_count": summary.get("direct_locked_side_taker_candidate_count"),
        "direct_locked_side_missing_ask_count": summary.get("direct_locked_side_missing_ask_count"),
        "dead_side_bid_available_count": summary.get("dead_side_bid_available_count"),
        "dead_side_capture_candidate_count": summary.get("dead_side_capture_candidate_count"),
        "dead_side_capture_positive_edge_count": summary.get("dead_side_capture_positive_edge_count"),
        "dead_side_capture_total_bid_depth": dead_side_total_bid_depth,
        "dead_side_capture_top_samples": [
            row
            for row in dead_side_watch_rows
            if row.get("dead_side_best_bid") is not None
        ][:20],
        "dead_side_capture_blocker_counts": _counter_rows(dead_side_blockers, "blocker"),
        "execution_mode_status": "diagnostic_only_until_ctf_split_merge_supported",
        "diagnostic_fill_count": dead_side_diagnostic_fill_count,
        "market_already_reflected_lock_count": summary.get("market_already_reflected_lock_count"),
        "paths": {
            "dead_side_capture_watch_rows": str(dead_side_watch_rows_path),
            "dead_side_capture_diagnostic_fills": str(dead_side_diagnostic_fills_path),
        },
    }
    dead_side_capture_report_path = archive_root / "dead_side_capture_sampler_report.json"
    _write_json(dead_side_capture_report_path, dead_side_capture_report)
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "scanned_row_count": len(source_rows),
        "intraday_visible_station_count": summary.get("intraday_visible_station_count"),
        "locked_count": summary.get("locked_count"),
        "ge_le_locked_count": summary.get("ge_le_locked_count"),
        "stale_warning_count": summary.get("stale_warning_count"),
        "stale_blocker_count": summary.get("stale_blocker_count"),
        "direct_locked_side_book_count": summary.get("direct_locked_side_book_count"),
        "synthetic_locked_side_price_count": summary.get("synthetic_locked_side_price_count"),
        "missing_locked_side_orderbook_count": summary.get("missing_locked_side_orderbook_count"),
        "direct_locked_side_taker_candidate_count": summary.get("direct_locked_side_taker_candidate_count"),
        "direct_locked_side_missing_ask_count": summary.get("direct_locked_side_missing_ask_count"),
        "dead_side_bid_available_count": summary.get("dead_side_bid_available_count"),
        "dead_side_capture_candidate_count": summary.get("dead_side_capture_candidate_count"),
        "dead_side_capture_positive_edge_count": summary.get("dead_side_capture_positive_edge_count"),
        "dead_side_capture_total_bid_depth": dead_side_total_bid_depth,
        "market_already_reflected_lock_count": summary.get("market_already_reflected_lock_count"),
        "observation_lock_execution_mode_breakdown": summary.get("execution_mode_counts"),
        "executable_candidate_count": len(candidates),
        "paper_fill_count": len(fills),
        "dead_side_capture_diagnostic_fill_count": dead_side_diagnostic_fill_count,
        "paper_fill_written_count": fills_written,
        "orderbook_snapshot_count": len(snapshot_records),
        "orderbook_snapshot_written_count": snapshots_written,
        "locked_watch_orderbook_snapshot_count": len(locked_watch_snapshot_records),
        "locked_watch_orderbook_snapshot_written_count": locked_watch_snapshots_written,
        "locked_watch_rows_written_count": locked_watch_rows_written,
        "dead_side_capture_watch_rows_written_count": dead_side_watch_rows_written,
        "reject_reason_counts": _counter_rows(reject_counts, "reason"),
        "ge_le_locked_reject_reason_counts": _counter_rows(ge_le_locked_reject_counts, "reason"),
        "locked_watch_reason_counts": _counter_rows(locked_watch_reason_counts, "reason"),
        "dead_side_capture_blocker_counts": _counter_rows(dead_side_blockers, "blocker"),
        "candidate_samples": candidates[:10],
        "top_watch_samples": watch_samples[:10],
        "top_locked_watch_samples": watch_samples[:10],
        "dead_side_capture_top_samples": dead_side_capture_report["dead_side_capture_top_samples"][:10],
        "execution_mode_status": "diagnostic_only_until_ctf_split_merge_supported",
        "metar_freshness_status": "fresh_candidates_required",
        "max_staleness_minutes": float(max_staleness_minutes),
        "paths": {
            "orderbook_snapshots": str(snapshot_path),
            "paper_fills": str(fill_path),
            "signal_report": str(signal_report_output) if signal_report_output else None,
            "locked_watch_orderbook_snapshots": str(locked_watch_snapshot_path),
            "locked_watch_rows": str(locked_watch_rows_path),
            "dead_side_capture_watch_rows": str(dead_side_watch_rows_path),
            "dead_side_capture_sampler_report": str(dead_side_capture_report_path),
            "dead_side_capture_diagnostic_fills": str(dead_side_diagnostic_fills_path),
        },
    }
    _write_json(Path(orderbook_archive_dir) / "observation_lock_execution_sampler_report.json", report)
    return report


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only observation-lock execution sampler.")
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--rows-json", default=None, help="Optional active rows payload for tests/offline replay.")
    parser.add_argument("--intraday-observation-path", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--paper-fill-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--signal-report-output", default=str(DEFAULT_ROOT / "latest_signal_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "observation_lock_execution_sampler_report.json"))
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--exclude-dust", action="store_true")
    parser.add_argument("--bucket-type", action="append", dest="bucket_types", default=None)
    parser.add_argument("--max-staleness-minutes", type=float, default=10.0)
    parser.add_argument("--min-executable-edge", type=float, default=0.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--collect-intraday-before-scan", action="store_true")
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
    parser.add_argument("--intraday-manifest-path", default="evidence/official_observations/manifest.json")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    if args.collect_intraday_before_scan:
        collection_report = _collect_intraday(args)
        if collection_report["returncode"] != 0:
            report = _collector_failed_report(
                generated_at=utc_now_iso(),
                collection_report=collection_report,
                summary_output=args.summary_output,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return
    loaded_rows = _load_rows_json(args.rows_json)
    if loaded_rows is None:
        payload = build_polymarket_weather_payload(
            queries=("temperature",),
            row_limit=max(1, int(args.polymarket_row_limit)),
            include_order_books=True,
            include_city_temperature_queries=True,
            max_city_temperature_queries=6,
        )
        loaded_rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    report = build_observation_lock_execution_sampler_report(
        rows=loaded_rows,
        intraday_observation_path=args.intraday_observation_path,
        orderbook_archive_dir=args.orderbook_archive_dir,
        paper_fill_dir=args.paper_fill_dir,
        signal_report_output=args.signal_report_output,
        max_candidates=int(args.max_candidates),
        bucket_types=args.bucket_types or ["ge", "le"],
        exclude_dust=bool(args.exclude_dust),
        max_staleness_minutes=float(args.max_staleness_minutes),
        min_executable_edge=float(args.min_executable_edge),
        max_spread=float(args.max_spread),
        min_ask_depth=float(args.min_ask_depth),
    )
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
