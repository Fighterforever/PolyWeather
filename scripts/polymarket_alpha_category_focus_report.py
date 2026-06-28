#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.category_focus_selector import (  # noqa: E402
    build_category_focus_report,
    load_json,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_MODEL = DEFAULT_ROOT / "probability_edge_model_report.json"
DEFAULT_DENSITY = DEFAULT_ROOT / "opportunity_density_scoreboard.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "category_focus_report.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select Polymarket categories for probability-edge forward paper.")
    parser.add_argument("--model-report", default=str(DEFAULT_MODEL))
    parser.add_argument("--opportunity-density", default=str(DEFAULT_DENSITY))
    parser.add_argument("--summary-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--min-sample-count", type=int, default=50)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_category_focus_report(
        model_report=load_json(args.model_report),
        opportunity_density_report=load_json(args.opportunity_density),
        min_sample_count=int(args.min_sample_count),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "top_categories_for_forward_paper": report.get("top_categories_for_forward_paper"),
                "category_count": report.get("category_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
