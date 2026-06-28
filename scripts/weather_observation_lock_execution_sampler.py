#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
    if row.get("latest_available_at") and not _is_fresh(
        row,
        generated_at=generated_at,
        max_staleness_minutes=max_staleness_minutes,
    ):
        reasons.append("stale_intraday_observation")
    if row.get("latest_available_at") is None:
        reasons.append("missing_intraday_available_at")
    if row.get("anomaly_flags"):
        reasons.append("blocked_by_observation_anomaly")
    if _safe_float(row.get("executable_edge")) is None or float(row.get("executable_edge") or 0.0) <= 0:
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
        "price_bucket": row.get("price_bucket"),
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id"),
        "settlement_spec": row.get("settlement_spec"),
        "no_lookahead": bool(row.get("no_lookahead") is not False),
    }


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
    candidates: List[Dict[str, Any]] = []
    watch_samples: List[Dict[str, Any]] = []
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
            if row.get("locked_side") and len(watch_samples) < 20:
                watch_samples.append({**row, "execution_sampler_reject_reasons": reasons})
    candidates = candidates[: max(0, int(max_candidates))]
    candidate_source_rows = [by_token.get(_text(row.get("token_id"))) for row in candidates]
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
    summary = signal_report.get("summary") if isinstance(signal_report.get("summary"), dict) else {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "scanned_row_count": len(source_rows),
        "intraday_visible_station_count": summary.get("intraday_visible_station_count"),
        "locked_count": summary.get("locked_count"),
        "executable_candidate_count": len(candidates),
        "paper_fill_count": len(fills),
        "paper_fill_written_count": fills_written,
        "orderbook_snapshot_count": len(snapshot_records),
        "orderbook_snapshot_written_count": snapshots_written,
        "reject_reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reject_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "candidate_samples": candidates[:10],
        "top_watch_samples": watch_samples[:10],
        "metar_freshness_status": "fresh_candidates_required",
        "max_staleness_minutes": float(max_staleness_minutes),
        "paths": {
            "orderbook_snapshots": str(snapshot_path),
            "paper_fills": str(fill_path),
            "signal_report": str(signal_report_output) if signal_report_output else None,
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
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
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
