#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.microstructure_policy_sweep import (  # noqa: E402
    build_microstructure_followup_snapshots,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_followup_snapshot_report.json"
DEFAULT_FOLLOWUP = DEFAULT_ROOT / "microstructure_followup_orderbook_snapshots.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append paper-only microstructure follow-up orderbook snapshots.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--taker-fills", default=str(DEFAULT_ROOT / "microstructure_paper_fills.jsonl"))
    parser.add_argument("--maker-quotes", default=str(DEFAULT_ROOT / "microstructure_maker_quotes.jsonl"))
    parser.add_argument("--watch-rows", default=str(DEFAULT_ROOT / "microstructure_watch_rows.jsonl"))
    parser.add_argument("--followup-output", default=str(DEFAULT_FOLLOWUP))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--max-watch-age-hours", type=float, default=24.0)
    return parser.parse_args(argv)


def _merge_jsonl(path: str | Path, rows: list[dict], key: str) -> int:
    existing = [row for row in load_jsonl(path) if row.get(key)]
    merged = {str(row[key]): row for row in existing}
    for row in rows:
        if isinstance(row, dict) and row.get(key):
            merged[str(row[key])] = row
    return write_jsonl(path, sorted(merged.values(), key=lambda row: str(row.get("recorded_at") or row.get("timestamp") or "")))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report = build_microstructure_followup_snapshots(
        active_markets=load_jsonl(args.active_markets),
        taker_fills=load_jsonl(args.taker_fills),
        maker_quotes=load_jsonl(args.maker_quotes),
        watch_rows=load_jsonl(args.watch_rows),
        recorded_at=generated_at,
        max_watch_age_hours=float(args.max_watch_age_hours),
    )
    _merge_jsonl(args.followup_output, report.get("snapshots") or [], "orderbook_snapshot_id")
    compact = {key: value for key, value in report.items() if key not in {"snapshots"}}
    compact["artifact_paths"] = {"microstructure_followup_orderbook_snapshots": str(args.followup_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "snapshot_count": report.get("snapshot_count"),
                "skipped_count": report.get("skipped_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
