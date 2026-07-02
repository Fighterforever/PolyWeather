#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.resolved_join_audit import (  # noqa: E402
    build_resolved_join_audit_report,
    load_json,
    load_jsonl,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_CLOSED = DEFAULT_ROOT / "closed_markets_snapshot.jsonl"
DEFAULT_SNAPSHOTS = DEFAULT_ROOT / "probability_decision_snapshots.jsonl"
DEFAULT_MANIFEST = DEFAULT_ROOT / "probability_decision_snapshot_manifest.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "resolved_join_audit_report.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit resolved outcome joins for Polymarket probability snapshots.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED))
    parser.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOTS))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_resolved_join_audit_report(
        snapshot_rows=load_jsonl(args.snapshots),
        market_rows=load_jsonl(args.active_markets) + load_jsonl(args.closed_markets),
        manifest=load_json(args.manifest),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "snapshot_row_count": report.get("snapshot_row_count"),
                "resolved_snapshot_count": report.get("resolved_snapshot_count"),
                "unique_resolved_event_family_count": report.get("unique_resolved_event_family_count"),
                "top_missing_outcome_reasons": report.get("missing_outcome_by_market_status", [])[:5],
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
