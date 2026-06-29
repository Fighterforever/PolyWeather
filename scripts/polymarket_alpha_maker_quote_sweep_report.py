#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.maker_quote_sweep import (  # noqa: E402
    build_maker_quote_aggressiveness_sweep,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_REPORT = DEFAULT_ROOT / "maker_quote_aggressiveness_sweep_report.json"
DEFAULT_ROWS = DEFAULT_ROOT / "maker_quote_aggressiveness_rows.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only maker quote aggressiveness counterfactual sweep.")
    parser.add_argument("--maker-quotes", default=str(DEFAULT_ROOT / "microstructure_maker_quotes.jsonl"))
    parser.add_argument("--orderbook-snapshots", default=str(DEFAULT_ROOT / "microstructure_orderbook_snapshots.jsonl"))
    parser.add_argument("--followup-orderbook-snapshots", default=str(DEFAULT_ROOT / "microstructure_followup_orderbook_snapshots.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROWS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_maker_quote_aggressiveness_sweep(
        maker_quotes=load_jsonl(args.maker_quotes),
        orderbook_snapshots=load_jsonl(args.orderbook_snapshots) + load_jsonl(args.followup_orderbook_snapshots),
    )
    write_jsonl(args.rows_output, report.get("rows") or [])
    compact = {key: value for key, value in report.items() if key != "rows"}
    compact["artifact_paths"] = {"rows": str(args.rows_output)}
    write_json(args.summary_output, compact)
    print(
        json.dumps(
            {
                "original_quote_count": compact.get("original_quote_count"),
                "sweep_quote_count": compact.get("sweep_quote_count"),
                "inferred_fill_count": compact.get("inferred_fill_count"),
                "best_mode": compact.get("best_mode"),
                "mode_recommendation": compact.get("mode_recommendation"),
                "live_order_path": compact.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
