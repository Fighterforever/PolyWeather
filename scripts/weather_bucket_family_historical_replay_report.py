#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_readonly import DEFAULT_WEATHER_QUERIES, build_polymarket_closed_weather_payload  # noqa: E402
from src.trading.weather_bucket_family_historical_replay import (  # noqa: E402
    build_bucket_family_historical_replay,
    load_jsonl,
)


DEFAULT_ROOT = Path("evidence/bucket_family")
DEFAULT_SUMMARY_OUTPUT = DEFAULT_ROOT / "basket_historical_replay_report.json"
DEFAULT_ROWS_OUTPUT = DEFAULT_ROOT / "basket_historical_replay_rows.jsonl"


def write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[Dict[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build approximate closed-market weather bucket-family replay.")
    parser.add_argument("--paper-only", action="store_true")
    parser.add_argument("--closed-rows-jsonl", default=None)
    parser.add_argument("--row-limit", type=int, default=1000)
    parser.add_argument("--search-limit-per-query", type=int, default=50)
    parser.add_argument("--max-city-temperature-queries", type=int, default=None)
    parser.add_argument("--min-edge-cents", type=float, default=1.0)
    parser.add_argument("--cost-cents", type=float, default=0.0)
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT))
    parser.add_argument("--rows-output", default=str(DEFAULT_ROWS_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.closed_rows_jsonl:
        rows = load_jsonl(args.closed_rows_jsonl)
        source = {"closed_rows_source": args.closed_rows_jsonl}
    else:
        payload = build_polymarket_closed_weather_payload(
            queries=DEFAULT_WEATHER_QUERIES,
            row_limit=args.row_limit,
            search_limit_per_query=args.search_limit_per_query,
            include_city_temperature_queries=True,
            max_city_temperature_queries=args.max_city_temperature_queries,
        )
        rows = payload.get("rows") or []
        source = {
            "closed_rows_source": "polymarket_closed_search",
            "payload_status": payload.get("status"),
            "payload_row_count": len(rows),
            "diagnostics": payload.get("diagnostics") or {},
        }
    report = build_bucket_family_historical_replay(
        rows,
        min_edge_cents=args.min_edge_cents,
        cost_cents=args.cost_cents,
    )
    report["source"] = source
    report["artifact_paths"] = {
        "summary_output": str(args.summary_output),
        "rows_output": str(args.rows_output),
    }
    write_json(args.summary_output, {k: v for k, v in report.items() if k != "rows"})
    write_jsonl(args.rows_output, report.get("rows") or [])
    print(json.dumps({k: report.get(k) for k in ("family_count", "replayable_family_count", "approximate_edge_candidate_count", "executable_depth_available_count", "approximate_pnl_cents")}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
