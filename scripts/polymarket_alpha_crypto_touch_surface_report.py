#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_touch_surface import (  # noqa: E402
    build_crypto_touch_surface_report,
    load_json,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build crypto touch barrier surface relative-value report.")
    parser.add_argument("--crypto-report", default=str(DEFAULT_ROOT / "crypto_probability_edge_report.json"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_touch_surface_report.json"))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROOT / "crypto_touch_surface_rows.jsonl"))
    parser.add_argument("--min-edge", type=float, default=0.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_crypto_touch_surface_report(
        crypto_probability_report=load_json(args.crypto_report),
        min_edge=float(args.min_edge),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    report["artifact_paths"] = {
        "crypto_report": str(args.crypto_report),
        "rows": str(args.rows_output),
    }
    write_json(args.summary_output, {key: value for key, value in report.items() if key != "rows"})
    print(
        json.dumps(
            {
                "group_count": report.get("group_count"),
                "market_count": report.get("market_count"),
                "monotonic_violation_count": report.get("monotonic_violation_count"),
                "relative_value_candidate_count": report.get("relative_value_candidate_count"),
                "surface_near_miss_count": report.get("surface_near_miss_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
