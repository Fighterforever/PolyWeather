#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_edge_model import (  # noqa: E402
    build_probability_edge_model_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_DATASET = DEFAULT_ROOT / "probability_dataset.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "probability_edge_model_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "probability_edge_candidates_historical.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train/validate Polymarket probability edge model.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--cost", type=float, default=0.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_probability_edge_model_report(load_jsonl(args.dataset), min_edge=float(args.min_edge), cost=float(args.cost))
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    compact = {key: value for key, value in report.items() if key not in {"candidates", "model"}}
    compact["model"] = report.get("model")
    compact["artifact_paths"] = {"probability_edge_candidates_historical": str(args.candidates_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "input_resolved_row_count": report.get("input_resolved_row_count"),
                "validate_row_count": report.get("validate_row_count"),
                "candidate_count": report.get("candidate_count"),
                "hard_conclusion": report.get("hard_conclusion"),
                "top_oos_brier_improvement_categories": [
                    row.get("category") for row in (report.get("top_oos_brier_improvement_categories") or [])[:5]
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
