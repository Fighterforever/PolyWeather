#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_orderbook_archive import build_orderbook_snapshot_records  # noqa: E402
from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_paper_journal import _append_jsonl, stable_json_hash, utc_now_iso  # noqa: E402
from src.trading.weather_threshold_latency_signal import build_threshold_latency_signal_report  # noqa: E402


SCHEMA_VERSION = "polyweather_threshold_latency_execution_sampler.v1"
DEFAULT_ROOT = Path("evidence/threshold_latency")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    materialized = [row for row in rows if isinstance(row, dict)]
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(materialized)


def _load_rows_json(path: Optional[str | Path]) -> Optional[list[dict]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def _counter_rows(counter: Counter[str], field: str) -> list[dict]:
    return [{field: key, "count": value} for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]


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
    }


def _paper_fill(row: Dict[str, Any], *, recorded_at: str, snapshot_id: Optional[str]) -> Dict[str, Any]:
    identity = {
        "strategy_id": "threshold_latency",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "signal_type": row.get("signal_type"),
        "current_available_at": row.get("current_available_at"),
    }
    return {
        "schema_version": "polyweather_threshold_latency_paper_fill.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_id": stable_json_hash(identity, length=24),
        "strategy_id": "threshold_latency",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "signal_type": row.get("signal_type"),
        "station_code": row.get("station_code"),
        "target_date": row.get("target_date"),
        "threshold": row.get("threshold"),
        "previous_high": row.get("previous_high"),
        "current_high": row.get("current_high"),
        "current_available_at": row.get("current_available_at"),
        "time_since_available_seconds": row.get("time_since_available_seconds"),
        "side_to_buy": row.get("side_to_buy"),
        "q_effective": row.get("q_effective"),
        "entry_price": row.get("q_effective"),
        "orderbook_snapshot_id": snapshot_id,
        "price_bucket": row.get("price_bucket"),
        "ask_depth": row.get("ask_depth"),
        "spread": row.get("spread"),
        "latency_edge_estimate": row.get("latency_edge_estimate"),
        "settlement_spec": row.get("settlement_spec"),
        "no_lookahead": True,
    }


def build_threshold_latency_execution_sampler_report(
    *,
    rows: Iterable[Dict[str, Any]],
    intraday_observation_path: str | Path,
    orderbook_archive_dir: str | Path = DEFAULT_ROOT,
    paper_fill_dir: str | Path = DEFAULT_ROOT,
    signal_report_output: Optional[str | Path] = None,
    generated_at: Optional[str] = None,
    max_candidates: int = 20,
    **signal_kwargs: Any,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    source_rows = [row for row in rows if isinstance(row, dict)]
    signal_report = build_threshold_latency_signal_report(
        source_rows,
        intraday_observation_path=intraday_observation_path,
        generated_at=generated_at,
        **signal_kwargs,
    )
    if signal_report_output:
        _write_json(signal_report_output, signal_report)
    signal_rows = [row for row in signal_report.get("rows") or [] if isinstance(row, dict)]
    candidates = [row for row in signal_rows if row.get("decision") == "candidate"][: max(0, int(max_candidates))]
    watch_rows = [row for row in signal_rows if row.get("decision") == "watch"]
    by_token = {_text(row.get("token_id")): row for row in source_rows if _text(row.get("token_id"))}
    candidate_source_rows = [by_token.get(_text(row.get("token_id"))) for row in candidates]
    candidate_source_rows = [row for row in candidate_source_rows if isinstance(row, dict)]
    archive_root = Path(orderbook_archive_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    snapshot_records = build_orderbook_snapshot_records(
        candidate_source_rows,
        recorded_at=generated_at,
        source_snapshot_id=f"threshold-latency-{generated_at}",
        source="threshold_latency_execution_sampler",
    )
    snapshot_path = archive_root / "orderbook_snapshots.jsonl"
    snapshot_path.touch(exist_ok=True)
    snapshot_written = _append_jsonl(snapshot_path, snapshot_records)
    snapshot_by_token = {_text(row.get("token_id")): row for row in snapshot_records}
    fills = [
        _paper_fill(
            row,
            recorded_at=generated_at,
            snapshot_id=(snapshot_by_token.get(_text(row.get("token_id"))) or {}).get("snapshot_id"),
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
    reject_counter: Counter[str] = Counter()
    for row in signal_rows:
        for blocker in row.get("blockers") or []:
            reject_counter[str(blocker)] += 1
    summary = signal_report.get("summary") if isinstance(signal_report.get("summary"), dict) else {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "scanned_row_count": len(source_rows),
        "metar_update_event_count": summary.get("metar_update_event_count"),
        "near_cross_count": summary.get("near_cross_count"),
        "just_crossed_count": summary.get("just_crossed_count"),
        "candidate_count": len(candidates),
        "paper_fill_count": len(fills),
        "paper_fill_written_count": fills_written,
        "watch_count": len(watch_rows),
        "watch_rows_written_count": watch_written,
        "orderbook_snapshot_count": len(snapshot_records),
        "orderbook_snapshot_written_count": snapshot_written,
        "reject_reason_counts": _counter_rows(reject_counter, "reason"),
        "candidate_samples": candidates[:10],
        "top_watch_samples": watch_rows[:10],
        "paths": {
            "signal_report": str(signal_report_output) if signal_report_output else None,
            "orderbook_snapshots": str(snapshot_path),
            "paper_fills": str(fill_path),
            "watch_rows": str(watch_path),
        },
    }
    _write_json(archive_root / "threshold_latency_execution_sampler_report.json", report)
    return report


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only threshold latency execution sampler.")
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--rows-json", default=None)
    parser.add_argument("--intraday-observation-path", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--intraday-manifest-path", default="evidence/official_observations/manifest.json")
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--paper-fill-dir", default=str(DEFAULT_ROOT))
    parser.add_argument("--signal-report-output", default=str(DEFAULT_ROOT / "threshold_latency_signal_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "threshold_latency_execution_sampler_report.json"))
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--collect-intraday-before-scan", action="store_true")
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
    parser.add_argument("--near-cross-margin-c", type=float, default=1.0)
    parser.add_argument("--near-break-margin-c", type=float, default=1.0)
    parser.add_argument("--max-update-age-minutes", type=float, default=5.0)
    parser.add_argument("--near-cross-probability", type=float, default=0.65)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    if args.collect_intraday_before_scan:
        collection = _collect_intraday(args)
        if collection["returncode"] != 0:
            report = {
                "schema_version": SCHEMA_VERSION,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "collector_failed": True,
                "collector_report": collection,
                "candidate_count": 0,
                "paper_fill_count": 0,
            }
            _write_json(args.summary_output, report)
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return
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
    report = build_threshold_latency_execution_sampler_report(
        rows=rows,
        intraday_observation_path=args.intraday_observation_path,
        orderbook_archive_dir=args.orderbook_archive_dir,
        paper_fill_dir=args.paper_fill_dir,
        signal_report_output=args.signal_report_output,
        max_candidates=int(args.max_candidates),
        near_cross_margin_c=float(args.near_cross_margin_c),
        near_break_margin_c=float(args.near_break_margin_c),
        max_update_age_minutes=float(args.max_update_age_minutes),
        near_cross_probability=float(args.near_cross_probability),
        max_spread=float(args.max_spread),
        min_ask_depth=float(args.min_ask_depth),
    )
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
