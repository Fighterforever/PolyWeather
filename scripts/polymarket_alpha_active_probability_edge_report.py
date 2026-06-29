#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.active_probability_edge_scanner import (  # noqa: E402
    load_json,
    load_jsonl,
    scan_active_probability_edges,
    write_json,
    write_jsonl,
)
from src.trading.polymarket_alpha.probability_edge_journal import build_probability_edge_fills_with_snapshots  # noqa: E402
from src.trading.polymarket_alpha.probability_edge_journal import build_formal_fill_followup_orderbook_snapshots  # noqa: E402
from src.trading.polymarket_alpha.probability_edge_journal import normalize_probability_edge_fill  # noqa: E402
from src.trading.polymarket_readonly import PolymarketReadonlyClient  # noqa: E402


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_MODEL = DEFAULT_ROOT / "probability_edge_model_report.json"
DEFAULT_FOCUS = DEFAULT_ROOT / "category_focus_report.json"
DEFAULT_CRYPTO = DEFAULT_ROOT / "crypto_probability_edge_report.json"
DEFAULT_CRYPTO_CANDIDATES = DEFAULT_ROOT / "crypto_probability_candidates.jsonl"
DEFAULT_SURFACE = DEFAULT_ROOT / "crypto_touch_surface_report.json"
DEFAULT_SENSITIVITY = DEFAULT_ROOT / "crypto_touch_sensitivity_report.json"
DEFAULT_REPORT = DEFAULT_ROOT / "active_probability_edge_report.json"
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan active Polymarket markets with the paper-only probability edge model.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--model-report", default=str(DEFAULT_MODEL))
    parser.add_argument("--focus-report", default=str(DEFAULT_FOCUS))
    parser.add_argument("--crypto-report", default=str(DEFAULT_CRYPTO))
    parser.add_argument("--crypto-candidates", default=str(DEFAULT_CRYPTO_CANDIDATES))
    parser.add_argument("--surface-report", default=str(DEFAULT_SURFACE))
    parser.add_argument("--sensitivity-report", default=str(DEFAULT_SENSITIVITY))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--fills-output", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--watch-rows-output", default=str(DEFAULT_PAPER / "watch_rows.jsonl"))
    parser.add_argument("--orderbook-snapshots-output", default=str(DEFAULT_PAPER / "orderbook_snapshots.jsonl"))
    parser.add_argument("--fill-followup-orderbook-snapshots-output", default=str(DEFAULT_PAPER / "fill_followup_orderbook_snapshots.jsonl"))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--stricter-edge", type=float, default=0.02)
    parser.add_argument("--min-depth", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.15)
    parser.add_argument("--cost", type=float, default=0.01)
    return parser.parse_args(argv)


def _merge_jsonl_by_key(path: str | Path, rows: list[dict], key: str) -> int:
    existing = [row for row in load_jsonl(path) if row.get(key)]
    merged = {str(row[key]): row for row in existing}
    for row in rows:
        if isinstance(row, dict) and row.get(key):
            merged[str(row[key])] = row
    return write_jsonl(
        path,
        sorted(
            merged.values(),
            key=lambda row: str(row.get("timestamp") or row.get("recorded_at") or row.get("entry_time") or ""),
        ),
    )


def _fill_identity(row: dict) -> str | None:
    if not isinstance(row, dict):
        return None
    market_slug = row.get("market_slug")
    token_id = row.get("token_id")
    side = row.get("side")
    if market_slug and token_id and side:
        return f"{market_slug}|{token_id}|{side}"
    fill_id = row.get("fill_id")
    if fill_id:
        return str(fill_id)
    return None


def _merge_probability_edge_fills(existing_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    anonymous: list[dict] = []
    for row in list(existing_rows or []) + list(new_rows or []):
        if not isinstance(row, dict):
            continue
        identity = _fill_identity(row)
        if not identity:
            anonymous.append(row)
            continue
        if identity not in merged:
            merged[identity] = row
    materialized = anonymous + list(merged.values())
    return sorted(
        materialized,
        key=lambda row: str(row.get("entry_time") or row.get("timestamp") or row.get("generated_at") or ""),
    )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    crypto_report = load_json(args.crypto_report)
    crypto_candidates = load_jsonl(args.crypto_candidates)
    if crypto_candidates:
        crypto_report["candidates"] = crypto_candidates
    active_markets = load_jsonl(args.active_markets)
    report = scan_active_probability_edges(
        active_markets=active_markets,
        model_report=load_json(args.model_report),
        focus_report=load_json(args.focus_report),
        crypto_report=crypto_report,
        surface_report=load_json(args.surface_report),
        sensitivity_report=load_json(args.sensitivity_report),
        min_edge=float(args.min_edge),
        stricter_edge=float(args.stricter_edge),
        min_depth=float(args.min_depth),
        max_spread=float(args.max_spread),
        cost=float(args.cost),
    )
    journal = build_probability_edge_fills_with_snapshots(
        candidates=report.get("candidates") or [],
        active_markets=active_markets,
        recorded_at=args.generated_at,
    )
    new_fills = journal.get("fills") or []
    existing_fills = load_jsonl(args.fills_output)
    merged_fills = [normalize_probability_edge_fill(row) for row in _merge_probability_edge_fills(existing_fills, new_fills)]
    followup = build_formal_fill_followup_orderbook_snapshots(
        fills=merged_fills,
        active_markets=active_markets,
        recorded_at=args.generated_at,
        orderbook_fetcher=PolymarketReadonlyClient().get_order_book,
        min_edge=float(args.min_edge),
    )
    _merge_jsonl_by_key(
        args.orderbook_snapshots_output,
        journal.get("orderbook_snapshots") or [],
        "orderbook_snapshot_id",
    )
    write_jsonl(args.fills_output, merged_fills)
    write_jsonl(args.watch_rows_output, report.get("watch_rows") or [])
    _merge_jsonl_by_key(
        args.fill_followup_orderbook_snapshots_output,
        followup.get("snapshots") or [],
        "orderbook_snapshot_id",
    )
    report["new_paper_fill_count"] = len(new_fills)
    report["formal_fill_count"] = len(merged_fills)
    report["paper_fill_count"] = len(merged_fills)
    report["candidate_count"] = len(new_fills)
    report["orderbook_snapshot_id_null_count"] = journal.get("orderbook_snapshot_id_null_count")
    report["rejected_fill_count"] = journal.get("rejected_fill_count")
    report["rejected_fills"] = journal.get("rejected_fills") or []
    report["formal_fill_followup_snapshot_count"] = followup.get("snapshot_count")
    report["formal_fill_followup_skipped_count"] = followup.get("skipped_count")
    report["formal_fill_followup_skipped"] = followup.get("skipped") or []
    report["artifact_paths"] = {
        "fills": str(args.fills_output),
        "watch_rows": str(args.watch_rows_output),
        "orderbook_snapshots": str(args.orderbook_snapshots_output),
        "fill_followup_orderbook_snapshots": str(args.fill_followup_orderbook_snapshots_output),
        "surface_report": str(args.surface_report),
        "sensitivity_report": str(args.sensitivity_report),
    }
    write_json(args.summary_output, {key: value for key, value in report.items() if key not in {"candidates", "watch_rows"}})
    print(
        json.dumps(
            {
                "candidate_count": report.get("candidate_count"),
                "scanned_active_market_count": report.get("scanned_active_market_count"),
                "model_ready_count": report.get("model_ready_count"),
                "paper_fill_count": report.get("paper_fill_count"),
                "old_formal_fill_count": report.get("old_formal_fill_count"),
                "new_formal_fill_count": report.get("new_formal_fill_count"),
                "downgraded_due_surface_count": report.get("downgraded_due_surface_count"),
                "downgraded_due_sensitivity_count": report.get("downgraded_due_sensitivity_count"),
                "watch_count": report.get("watch_count"),
                "focus_categories": report.get("focus_categories"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
