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
from src.trading.weather_observation_lock_signal import build_observation_lock_signal_report  # noqa: E402
from src.trading.weather_paper_journal import _append_jsonl, stable_json_hash, utc_now_iso  # noqa: E402


SCHEMA_VERSION = "polyweather_eq_dead_no_execution_sampler.v1"
DEFAULT_ROOT = Path("evidence/eq_dead_no")


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


def build_eq_dead_no_execution_sampler_report(
    *,
    rows: Iterable[Dict[str, Any]],
    intraday_observation_path: str | Path,
    orderbook_archive_dir: str | Path = DEFAULT_ROOT,
    paper_fill_dir: str | Path = DEFAULT_ROOT,
    signal_report_output: Optional[str | Path] = None,
    generated_at: Optional[str] = None,
    max_candidates: int = 20,
    max_entry_price: float = 0.95,
    min_ask_depth: float = 1.0,
    max_spread: float = 0.03,
    exclude_dust: bool = True,
    default_cost: float = 0.005,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    source_rows = [row for row in rows if isinstance(row, dict)]
    signal_report = build_observation_lock_signal_report(
        source_rows,
        intraday_repository=None,
        observations=_load_intraday_rows(intraday_observation_path),
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
    report = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "scanned_eq_rows": len(eq_rows),
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
        "paths": {
            "signal_report": str(signal_report_output) if signal_report_output else None,
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
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--max-entry-price", type=float, default=0.95)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--exclude-dust", action="store_true")
    parser.add_argument("--collect-intraday-before-scan", action="store_true")
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
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
                "executable_candidate_count": 0,
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
    report = build_eq_dead_no_execution_sampler_report(
        rows=rows,
        intraday_observation_path=args.intraday_observation_path,
        orderbook_archive_dir=args.orderbook_archive_dir,
        paper_fill_dir=args.paper_fill_dir,
        signal_report_output=args.signal_report_output,
        max_candidates=args.max_candidates,
        max_entry_price=args.max_entry_price,
        min_ask_depth=args.min_ask_depth,
        max_spread=args.max_spread,
        exclude_dust=args.exclude_dust,
    )
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
