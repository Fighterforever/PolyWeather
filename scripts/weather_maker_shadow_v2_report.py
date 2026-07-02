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

from src.trading.polymarket_readonly import build_polymarket_weather_payload  # noqa: E402
from src.trading.weather_maker_shadow_v2 import (  # noqa: E402
    build_maker_shadow_v2_report,
    write_json,
    write_jsonl,
)
from src.trading.weather_paper_journal import load_jsonl  # noqa: E402


DEFAULT_ROOT = Path("evidence/maker_shadow_v2")
DEFAULT_QUOTES = DEFAULT_ROOT / "quotes.jsonl"
DEFAULT_FILLS = DEFAULT_ROOT / "fills.jsonl"
DEFAULT_MARKOUTS = DEFAULT_ROOT / "markouts.jsonl"
DEFAULT_REPORT = DEFAULT_ROOT / "report.json"
DEFAULT_OBSERVATIONS = Path("evidence/official_observations/intraday_observations.jsonl")


def _load_rows_json(path: Optional[str | Path]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [row for row in rows or [] if isinstance(row, dict)]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build paper-only Polymarket weather maker shadow v2 report.")
    parser.add_argument("--rows-json", default=None)
    parser.add_argument("--previous-quotes", default=str(DEFAULT_QUOTES))
    parser.add_argument("--intraday-observation-path", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--quotes-output", default=str(DEFAULT_QUOTES))
    parser.add_argument("--fills-output", default=str(DEFAULT_FILLS))
    parser.add_argument("--markouts-output", default=str(DEFAULT_MARKOUTS))
    parser.add_argument("--polymarket-row-limit", type=int, default=240)
    parser.add_argument("--min-spread", type=float, default=0.02)
    parser.add_argument("--min-bid-depth", type=float, default=1.0)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--maker-margin", type=float, default=0.006)
    parser.add_argument("--adverse-selection-haircut", type=float, default=0.002)
    parser.add_argument("--cost-cents", type=float, default=0.0)
    parser.add_argument("--estimated-rebate-cents", type=float, default=0.0)
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
    previous_quotes = load_jsonl(args.previous_quotes)
    observations = load_jsonl(args.intraday_observation_path)
    report = build_maker_shadow_v2_report(
        rows,
        previous_quotes=previous_quotes,
        observations=observations,
        generated_at=args.generated_at,
        min_spread=float(args.min_spread),
        min_bid_depth=float(args.min_bid_depth),
        min_ask_depth=float(args.min_ask_depth),
        maker_margin=float(args.maker_margin),
        adverse_selection_haircut=float(args.adverse_selection_haircut),
        cost_cents=float(args.cost_cents),
        estimated_rebate_cents=float(args.estimated_rebate_cents),
    )
    write_json(args.summary_output, {key: value for key, value in report.items() if key not in {"quotes", "fills", "markouts"}})
    write_jsonl(args.quotes_output, report.get("quotes") or [])
    write_jsonl(args.fills_output, report.get("fills") or [])
    write_jsonl(args.markouts_output, report.get("markouts") or [])
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "quote_count",
                    "inferred_fill_count",
                    "markout_count",
                    "mean_markout_without_rebate",
                    "mean_markout_with_rebate",
                    "adverse_selection_count",
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
