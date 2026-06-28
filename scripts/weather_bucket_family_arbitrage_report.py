#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_readonly import DEFAULT_WEATHER_QUERIES, build_polymarket_weather_payload  # noqa: E402
from src.trading.polymarket_orderbook_archive import write_orderbook_archive_from_payload  # noqa: E402
from src.trading.weather_basket_paper_journal import write_basket_paper_fills  # noqa: E402
from src.trading.weather_bucket_family import build_weather_bucket_family_catalog  # noqa: E402
from src.trading.weather_bucket_family_arbitrage import (  # noqa: E402
    build_bucket_family_arbitrage_report,
    load_bucket_family_catalog,
)
from src.trading.weather_paper_journal import utc_now_iso  # noqa: E402


DEFAULT_ROOT = Path("evidence/bucket_family")
DEFAULT_SUMMARY_OUTPUT = DEFAULT_ROOT / "latest_arbitrage_report.json"
DEFAULT_CANDIDATES_OUTPUT = DEFAULT_ROOT / "basket_arbitrage_candidates.jsonl"
DEFAULT_WATCH_OUTPUT = DEFAULT_ROOT / "basket_arbitrage_watch_rows.jsonl"
DEFAULT_MONOTONIC_OUTPUT = DEFAULT_ROOT / "monotonic_arbitrage_candidates.jsonl"
DEFAULT_CATALOG_OUTPUT = DEFAULT_ROOT / "weather_bucket_family_catalog.json"


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]], *, append: bool = False) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with target.open(mode, encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def _load_or_fetch_catalog(
    *,
    catalog_path: Optional[str],
    catalog_output: str | Path,
    orderbook_archive_dir: str | Path,
    row_limit: int,
    search_limit_per_query: int,
    active_scan_limit: int,
    include_order_books: bool,
    include_city_temperature_queries: bool,
    max_city_temperature_queries: Optional[int],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if catalog_path:
        return load_bucket_family_catalog(catalog_path), {"catalog_source": str(catalog_path)}
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
    archive_manifest = write_orderbook_archive_from_payload(payload, archive_dir=orderbook_archive_dir) if include_order_books else {}
    catalog = build_weather_bucket_family_catalog(payload.get("rows") or [])
    catalog["scan_payload_summary"] = {
        "snapshot_id": payload.get("snapshot_id"),
        "generated_at": payload.get("generated_at"),
        "status": payload.get("status"),
        "row_count": len(payload.get("rows") or []),
        "diagnostics": payload.get("diagnostics") or {},
        "include_order_books": bool(include_order_books),
    }
    write_json(catalog_output, catalog)
    return catalog, {
        "catalog_source": "active_polymarket_scan",
        "catalog_output": str(catalog_output),
        "orderbook_archive_manifest": archive_manifest,
    }


def build_bucket_family_arbitrage_cli_report(
    *,
    catalog_path: Optional[str] = None,
    catalog_output: str | Path = DEFAULT_CATALOG_OUTPUT,
    orderbook_archive_dir: str | Path = DEFAULT_ROOT / "orderbook_archive",
    paper_fill_dir: str | Path = DEFAULT_ROOT / "basket_paper",
    min_edge_cents: float = 1.0,
    min_leg_depth: float = 1.0,
    cost_cents: float = 0.0,
    max_candidates: int = 20,
    row_limit: int = 180,
    search_limit_per_query: int = 50,
    active_scan_limit: int = 1000,
    include_order_books: bool = True,
    include_city_temperature_queries: bool = True,
    max_city_temperature_queries: Optional[int] = None,
    write_paper_fills: bool = True,
) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    catalog, input_info = _load_or_fetch_catalog(
        catalog_path=catalog_path,
        catalog_output=catalog_output,
        orderbook_archive_dir=orderbook_archive_dir,
        row_limit=row_limit,
        search_limit_per_query=search_limit_per_query,
        active_scan_limit=active_scan_limit,
        include_order_books=include_order_books,
        include_city_temperature_queries=include_city_temperature_queries,
        max_city_temperature_queries=max_city_temperature_queries,
    )
    report = build_bucket_family_arbitrage_report(
        catalog,
        min_edge_cents=min_edge_cents,
        min_leg_depth=min_leg_depth,
        cost_cents=cost_cents,
        max_candidates=max_candidates,
    )
    paper_report = (
        write_basket_paper_fills(report.get("candidates") or [], paper_fill_dir=paper_fill_dir, created_at=generated_at)
        if write_paper_fills and report.get("candidate_count", 0) > 0
        else {
            "schema_version": "polyweather_weather_basket_paper_journal.v1",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "basket_paper_fill_count": 0,
            "written_count": 0,
            "fills": [],
            "fills_path": str(Path(paper_fill_dir) / "fills.jsonl"),
            "manifest_path": str(Path(paper_fill_dir) / "manifest.jsonl"),
        }
    )
    report.update(
        {
            "generated_at": generated_at,
            "scan_inputs": input_info,
            "thresholds": {
                "min_edge_cents": float(min_edge_cents),
                "min_leg_depth": float(min_leg_depth),
                "cost_cents": float(cost_cents),
                "max_candidates": int(max_candidates),
            },
            "basket_paper_fill_count": int(paper_report.get("basket_paper_fill_count") or 0),
            "paper_journal": {
                "fills_path": paper_report.get("fills_path"),
                "manifest_path": paper_report.get("manifest_path"),
                "written_count": paper_report.get("written_count"),
            },
        }
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan weather bucket family structural arbitrage, paper-only.")
    parser.add_argument("--paper-only", action="store_true", help="Explicit paper-only marker; no live path exists.")
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--catalog-output", default=str(DEFAULT_CATALOG_OUTPUT))
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ROOT / "orderbook_archive"))
    parser.add_argument("--paper-fill-dir", default=str(DEFAULT_ROOT / "basket_paper"))
    parser.add_argument("--min-edge-cents", type=float, default=1.0)
    parser.add_argument("--min-leg-depth", type=float, default=1.0)
    parser.add_argument("--cost-cents", type=float, default=0.0)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--row-limit", type=int, default=180)
    parser.add_argument("--search-limit-per-query", type=int, default=50)
    parser.add_argument("--active-scan-limit", type=int, default=1000)
    parser.add_argument("--max-city-temperature-queries", type=int, default=None)
    parser.add_argument("--no-city-temperature-queries", action="store_true")
    parser.add_argument("--no-order-books", action="store_true")
    parser.add_argument("--no-paper-fills", action="store_true")
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES_OUTPUT))
    parser.add_argument("--watch-output", default=str(DEFAULT_WATCH_OUTPUT))
    parser.add_argument("--monotonic-candidates-output", default=str(DEFAULT_MONOTONIC_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_bucket_family_arbitrage_cli_report(
        catalog_path=args.catalog,
        catalog_output=args.catalog_output,
        orderbook_archive_dir=args.orderbook_archive_dir,
        paper_fill_dir=args.paper_fill_dir,
        min_edge_cents=args.min_edge_cents,
        min_leg_depth=args.min_leg_depth,
        cost_cents=args.cost_cents,
        max_candidates=args.max_candidates,
        row_limit=args.row_limit,
        search_limit_per_query=args.search_limit_per_query,
        active_scan_limit=args.active_scan_limit,
        include_order_books=not args.no_order_books,
        include_city_temperature_queries=not args.no_city_temperature_queries,
        max_city_temperature_queries=args.max_city_temperature_queries,
        write_paper_fills=not args.no_paper_fills,
    )
    artifact_paths = {
        "summary_output": str(args.summary_output),
        "candidates_output": str(args.candidates_output),
        "watch_output": str(args.watch_output),
        "monotonic_candidates_output": str(args.monotonic_candidates_output),
        "catalog_output": str(args.catalog_output),
    }
    report["artifact_paths"] = artifact_paths
    write_json(args.summary_output, report)
    write_jsonl(args.candidates_output, report.get("candidates") or [], append=False)
    write_jsonl(args.watch_output, (report.get("basket_rows") or []) + (report.get("monotonic_rows") or []), append=False)
    write_jsonl(
        args.monotonic_candidates_output,
        [row for row in report.get("candidates") or [] if row.get("strategy_id") == "monotonic_threshold_pair"],
        append=False,
    )
    print(json.dumps({k: report.get(k) for k in ("family_count", "partition_family_count", "candidate_count", "best_edge_cents", "basket_paper_fill_count")}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
