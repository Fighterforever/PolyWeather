#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.maker_shadow import (  # noqa: E402
    build_maker_shadow_focus_report,
    load_json,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_FOCUS = DEFAULT_ROOT / "category_focus_report.json"
DEFAULT_MODEL = DEFAULT_ROOT / "probability_edge_model_report.json"
DEFAULT_OUTPUT_DIR = DEFAULT_ROOT / "maker_shadow_focus"
DEFAULT_REPORT = DEFAULT_OUTPUT_DIR / "report.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run maker shadow only on probability-edge focus categories.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--focus-report", default=str(DEFAULT_FOCUS))
    parser.add_argument("--model-report", default=str(DEFAULT_MODEL))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--quotes-output", default=str(DEFAULT_OUTPUT_DIR / "quotes.jsonl"))
    parser.add_argument("--fills-output", default=str(DEFAULT_OUTPUT_DIR / "inferred_fills.jsonl"))
    parser.add_argument("--markouts-output", default=str(DEFAULT_OUTPUT_DIR / "markouts.jsonl"))
    parser.add_argument("--min-spread", type=float, default=0.03)
    parser.add_argument("--min-depth", type=float, default=10.0)
    parser.add_argument("--maker-margin", type=float, default=0.01)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_maker_shadow_focus_report(
        active_markets=load_jsonl(args.active_markets),
        focus_report=load_json(args.focus_report),
        model_report=load_json(args.model_report),
        min_spread=float(args.min_spread),
        min_depth=float(args.min_depth),
        maker_margin=float(args.maker_margin),
    )
    write_jsonl(args.quotes_output, report.get("quotes") or [])
    write_jsonl(args.fills_output, report.get("inferred_fills") or [])
    write_jsonl(args.markouts_output, report.get("markouts") or [])
    report["artifact_paths"] = {
        "quotes": str(args.quotes_output),
        "inferred_fills": str(args.fills_output),
        "markouts": str(args.markouts_output),
    }
    write_json(args.summary_output, {key: value for key, value in report.items() if key not in {"quotes", "inferred_fills", "markouts"}})
    print(
        json.dumps(
            {
                "quote_count": report.get("quote_count"),
                "inferred_fill_count": report.get("inferred_fill_count"),
                "mean_markout_without_rebate": report.get("mean_markout_without_rebate"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
