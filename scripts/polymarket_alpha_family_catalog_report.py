#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.market_family_catalog import (  # noqa: E402
    build_market_family_catalog,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_ACTIVE = DEFAULT_ROOT / "active_markets_snapshot.jsonl"
DEFAULT_CLOSED = DEFAULT_ROOT / "closed_markets_snapshot.jsonl"
DEFAULT_CATALOG = DEFAULT_ROOT / "market_family_catalog.json"
DEFAULT_GAPS = DEFAULT_ROOT / "market_family_gaps.json"
DEFAULT_FAMILY_ROWS = DEFAULT_ROOT / "market_family_rows.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Polymarket market family catalog.")
    parser.add_argument("--active-markets", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED))
    parser.add_argument("--summary-output", default=str(DEFAULT_CATALOG))
    parser.add_argument("--gaps-output", default=str(DEFAULT_GAPS))
    parser.add_argument("--family-rows-output", default=str(DEFAULT_FAMILY_ROWS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    rows = load_jsonl(args.active_markets) + load_jsonl(args.closed_markets)
    catalog = build_market_family_catalog(rows)
    gap_rows = catalog.get("gaps") or []
    gap_reason_counts: dict[str, int] = {}
    for row in gap_rows:
        for reason in row.get("gap_reasons") or []:
            gap_reason_counts[str(reason)] = gap_reason_counts.get(str(reason), 0) + 1
    gaps = {
        "schema_version": "polyweather_polymarket_alpha_market_family_gaps.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "gap_count": len(gap_rows),
        "gap_reason_counts": [
            {"reason": reason, "count": count} for reason, count in sorted(gap_reason_counts.items())
        ],
        "sample_gaps": gap_rows[:500],
    }
    write_json(args.gaps_output, gaps)
    family_rows = catalog.get("families") or []
    write_jsonl(args.family_rows_output, family_rows)
    compact_catalog = {key: value for key, value in catalog.items() if key not in {"families", "gaps"}}
    compact_catalog["family_rows_count"] = len(family_rows)
    compact_catalog["artifact_paths"] = {
        "market_family_gaps": str(args.gaps_output),
        "market_family_rows": str(args.family_rows_output),
    }
    write_json(args.summary_output, compact_catalog)
    print(
        json.dumps(
            {
                "family_count": catalog.get("family_count"),
                "family_type_counts": catalog.get("family_type_counts"),
                "top_family_categories": [row.get("category") for row in (catalog.get("top_families") or [])[:5]],
                "live_order_path": catalog.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
