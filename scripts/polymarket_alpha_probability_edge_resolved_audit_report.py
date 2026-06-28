#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_edge_journal import (  # noqa: E402
    build_resolved_audit_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit resolved outcomes for paper-only probability edge fills.")
    parser.add_argument("--fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--dataset", default=str(DEFAULT_ROOT / "probability_dataset.jsonl"))
    parser.add_argument("--audits-output", default=str(DEFAULT_PAPER / "resolved_audits.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_PAPER / "resolved_audit_report.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_resolved_audit_report(fills=load_jsonl(args.fills), dataset_rows=load_jsonl(args.dataset))
    write_jsonl(args.audits_output, report.get("audits") or [])
    report["artifact_paths"] = {"resolved_audits": str(args.audits_output)}
    write_json(args.summary_output, {key: value for key, value in report.items() if key != "audits"})
    print(
        json.dumps(
            {
                "fill_count": report.get("fill_count"),
                "resolved_fill_count": report.get("resolved_fill_count"),
                "resolved_pnl_cents": report.get("resolved_pnl_cents"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
