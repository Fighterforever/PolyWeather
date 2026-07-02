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

from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_threshold_latency_signal import build_threshold_latency_signal_report  # noqa: E402


def _write_json(path: str | Path, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_rows_json(path: Optional[str | Path]) -> Optional[list[dict]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only threshold-latency signal report.")
    parser.add_argument("--rows-json", default=None)
    parser.add_argument("--intraday-observation-path", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--summary-output", default="evidence/threshold_latency/threshold_latency_signal_report.json")
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--near-cross-margin-c", type=float, default=1.0)
    parser.add_argument("--near-break-margin-c", type=float, default=1.0)
    parser.add_argument("--max-update-age-minutes", type=float, default=5.0)
    parser.add_argument("--near-cross-probability", type=float, default=0.65)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
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
            max_city_temperature_queries=6,
        )
        rows = [row for row in payload.get("rows") or [] if isinstance(row, dict)]
    report = build_threshold_latency_signal_report(
        rows,
        intraday_observation_path=args.intraday_observation_path,
        generated_at=args.generated_at,
        near_cross_margin_c=float(args.near_cross_margin_c),
        near_break_margin_c=float(args.near_break_margin_c),
        max_update_age_minutes=float(args.max_update_age_minutes),
        near_cross_probability=float(args.near_cross_probability),
        max_spread=float(args.max_spread),
        min_ask_depth=float(args.min_ask_depth),
    )
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
