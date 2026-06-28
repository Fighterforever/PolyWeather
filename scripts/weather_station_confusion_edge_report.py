#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_station_confusion_edge import (  # noqa: E402
    build_station_confusion_edge_report,
    load_jsonl,
    write_json,
    write_jsonl,
)


DEFAULT_ROOT = Path("evidence/station_confusion")
DEFAULT_BIAS = DEFAULT_ROOT / "station_bias_table.json"
DEFAULT_REPORT = DEFAULT_ROOT / "station_confusion_edge_report.json"
DEFAULT_CANDIDATES = DEFAULT_ROOT / "candidates.jsonl"
DEFAULT_STATION_ROWS = Path("evidence/official_observations/intraday_observations.jsonl")
DEFAULT_CITY_GRID_ROWS = Path("evidence/historical_forecasts/open_meteo_forecasts.jsonl")


def _load_rows_json(path: Optional[str | Path]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Polymarket station-confusion edge report.")
    parser.add_argument("--rows-json", default=None)
    parser.add_argument("--station-observations", default=str(DEFAULT_STATION_ROWS))
    parser.add_argument("--city-grid-observations", default=str(DEFAULT_CITY_GRID_ROWS))
    parser.add_argument("--bias-output", default=str(DEFAULT_BIAS))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--candidates-output", default=str(DEFAULT_CANDIDATES))
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--min-abs-confusion-edge", type=float, default=0.35)
    parser.add_argument("--min-bias-sample-count", type=int, default=3)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    rows = _load_rows_json(args.rows_json)
    if rows is None:
        payload = build_polymarket_weather_payload(
            queries=("temperature",),
            row_limit=max(1, int(args.polymarket_row_limit)),
            include_order_books=True,
            include_city_temperature_queries=True,
            max_city_temperature_queries=8,
        )
        rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    report = build_station_confusion_edge_report(
        rows,
        station_rows=load_jsonl(args.station_observations),
        city_grid_rows=load_jsonl(args.city_grid_observations),
        generated_at=args.generated_at,
        min_abs_confusion_edge=float(args.min_abs_confusion_edge),
        min_bias_sample_count=int(args.min_bias_sample_count),
        min_ask_depth=float(args.min_ask_depth),
    )
    bias_table = report.get("station_bias_table") if isinstance(report.get("station_bias_table"), dict) else {}
    write_json(args.bias_output, bias_table)
    write_json(args.summary_output, {key: value for key, value in report.items() if key not in {"candidates", "station_bias_table"}})
    write_jsonl(args.candidates_output, report.get("candidates") or [])
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "station_bias_sample_count",
                    "active_candidate_count",
                    "candidate_count",
                    "no_candidate_reason_counts",
                    "live_order_path",
                )
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
