#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_readonly import DEFAULT_WEATHER_QUERIES, build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_bucket_family import build_weather_bucket_family_catalog  # noqa: E402


DEFAULT_SUMMARY_OUTPUT = Path("evidence/bucket_family/weather_bucket_family_catalog.json")
DEFAULT_GAPS_OUTPUT = Path("evidence/bucket_family/weather_bucket_family_gaps.json")


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def build_bucket_family_catalog_report(
    *,
    row_limit: int = 1000,
    search_limit_per_query: int = 50,
    active_scan_limit: int = 1000,
    include_order_books: bool = True,
    include_city_temperature_queries: bool = True,
    max_city_temperature_queries: Optional[int] = None,
) -> Dict[str, Any]:
    payload = build_polymarket_weather_payload(
        queries=DEFAULT_WEATHER_QUERIES,
        row_limit=row_limit,
        search_limit_per_query=search_limit_per_query,
        include_city_temperature_queries=include_city_temperature_queries,
        max_city_temperature_queries=max_city_temperature_queries,
        active_scan_limit=active_scan_limit,
        include_order_books=include_order_books,
        exclude_expired_markets=True,
        exclude_not_accepting_orders=True,
    )
    report = build_weather_bucket_family_catalog(payload.get("rows") or [])
    report["scan_payload_summary"] = {
        "snapshot_id": payload.get("snapshot_id"),
        "generated_at": payload.get("generated_at"),
        "status": payload.get("status"),
        "row_count": len(payload.get("rows") or []),
        "diagnostics": payload.get("diagnostics") or {},
        "include_order_books": bool(include_order_books),
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a paper-only weather bucket family catalog.")
    parser.add_argument("--paper-only", action="store_true", help="Explicit paper-only marker; no live path exists.")
    parser.add_argument("--row-limit", type=int, default=1000)
    parser.add_argument("--search-limit-per-query", type=int, default=50)
    parser.add_argument("--active-scan-limit", type=int, default=1000)
    parser.add_argument("--max-city-temperature-queries", type=int, default=None)
    parser.add_argument("--no-city-temperature-queries", action="store_true")
    parser.add_argument("--no-order-books", action="store_true")
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT))
    parser.add_argument("--gaps-output", default=str(DEFAULT_GAPS_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_bucket_family_catalog_report(
        row_limit=args.row_limit,
        search_limit_per_query=args.search_limit_per_query,
        active_scan_limit=args.active_scan_limit,
        include_order_books=not args.no_order_books,
        include_city_temperature_queries=not args.no_city_temperature_queries,
        max_city_temperature_queries=args.max_city_temperature_queries,
    )
    write_json(args.summary_output, report)
    gaps = {
        "schema_version": "polyweather_weather_bucket_family_gaps.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "family_count": report.get("family_count"),
        "partition_candidate_count": report.get("partition_candidate_count"),
        "gap_counts": report.get("gap_counts") or [],
        "skipped_row_counts": report.get("skipped_row_counts") or [],
        "gaps": report.get("gaps") or [],
    }
    write_json(args.gaps_output, gaps)
    print(json.dumps({k: report.get(k) for k in ("family_count", "partition_candidate_count", "gap_counts")}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
