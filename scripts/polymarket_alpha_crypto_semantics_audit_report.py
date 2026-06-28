#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.crypto_market_semantics import (  # noqa: E402
    build_crypto_semantics_audit_report,
    load_jsonl,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit crypto probability paper fills against market resolution semantics.")
    parser.add_argument("--fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--active-markets", default=str(DEFAULT_ROOT / "active_markets_snapshot.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_semantics_audit_report.json"))
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_crypto_semantics_audit_report(
        fills=load_jsonl(args.fills),
        active_markets=load_jsonl(args.active_markets),
        generated_at=args.generated_at,
    )
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "fill_count": report.get("fill_count"),
                "touch_barrier_count": report.get("touch_barrier_count"),
                "semantics_mismatch_count": report.get("semantics_mismatch_count"),
                "invalidated_due_semantics_count": report.get("invalidated_due_semantics_count"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
