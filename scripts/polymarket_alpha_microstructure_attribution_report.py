#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.microstructure_experiment_analysis import (  # noqa: E402
    build_microstructure_taker_failure_attribution,
    load_jsonl,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "microstructure_taker_failure_attribution.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attribute negative microstructure taker paper markouts.")
    parser.add_argument("--paper-fills", default=str(DEFAULT_ROOT / "microstructure_paper_fills.jsonl"))
    parser.add_argument("--markouts", default=str(DEFAULT_ROOT / "microstructure_markouts.jsonl"))
    parser.add_argument("--policy-candidates", default=str(DEFAULT_ROOT / "microstructure_policy_candidates.jsonl"))
    parser.add_argument("--watch-rows", default=str(DEFAULT_ROOT / "microstructure_watch_rows.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_microstructure_taker_failure_attribution(
        fills=load_jsonl(args.paper_fills),
        markouts=load_jsonl(args.markouts),
        candidates=load_jsonl(args.policy_candidates),
        watch_rows=load_jsonl(args.watch_rows),
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "taker_fill_count": report.get("taker_fill_count"),
                "available_markout_count": report.get("available_markout_count"),
                "conclusion": report.get("conclusion"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
