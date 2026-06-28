#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.empirical_calibration import (  # noqa: E402
    build_empirical_calibration_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_DATASET = DEFAULT_ROOT / "probability_dataset.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "empirical_calibration_report.json"
DEFAULT_TABLE = DEFAULT_ROOT / "empirical_calibration_table.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build empirical Polymarket calibration curves.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--table-output", default=str(DEFAULT_TABLE))
    parser.add_argument("--min-sample-count", type=int, default=50)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_empirical_calibration_report(load_jsonl(args.dataset), min_sample_count=int(args.min_sample_count))
    write_jsonl(args.table_output, report.get("table") or [])
    compact = {key: value for key, value in report.items() if key != "table"}
    compact["artifact_paths"] = {"empirical_calibration_table": str(args.table_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "resolved_input_row_count": report.get("resolved_input_row_count"),
                "eligible_group_count": report.get("eligible_group_count"),
                "top_miscalibrated_categories": [
                    row.get("category") for row in (report.get("top_miscalibrated_groups") or [])[:5]
                ],
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
