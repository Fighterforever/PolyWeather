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


DEFAULT_OUTPUT = Path("evidence/web_first_alpha_research_report.json")


def _load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    try:
        parsed = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _count_jsonl(path: str | Path) -> int:
    source = Path(path)
    if not source.exists():
        return 0
    with source.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_web_first_alpha_research_report(args: argparse.Namespace) -> Dict[str, Any]:
    metar = _load_json(args.metar_manifest)
    metar_gaps = _load_json(args.metar_gap_report)
    closed = _load_json(args.closed_manifest)
    forecast = _load_json(args.forecast_manifest)
    price_gap = _load_json(args.price_gap_report)
    dataset = _load_json(args.dataset_manifest)
    replay = _load_json(args.observation_lock_summary)
    execution_sampling = _load_json(args.execution_sampling_manifest)
    resolved_pnl = replay.get("resolved_pnl_cents")
    if int(replay.get("resolved_fill_count") or 0) <= 0:
        obs_alpha = "no_resolved_executable_price_for_observation_lock"
    elif resolved_pnl is not None and float(resolved_pnl) > 0:
        obs_alpha = "positive_historical_replay_with_executable_price"
    elif resolved_pnl is not None and float(resolved_pnl) < 0:
        obs_alpha = "negative_historical_replay_with_executable_price"
    else:
        obs_alpha = "observation_lock_alpha_inconclusive"
    report = {
        "schema_version": "polyweather_web_first_alpha_research_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "data_source_coverage": {
            "metar_history": {
                "rows": _count_jsonl(args.metar_history),
                "station_count": metar.get("station_count"),
                "observation_count": metar.get("observation_count"),
                "date_coverage_count": len(metar.get("date_coverage") or []),
                "gap_count": metar_gaps.get("gap_count", metar.get("gap_count")),
            },
            "closed_polymarket_markets": {
                "rows": _count_jsonl(args.closed_markets),
                "closed_weather_market_count": closed.get("closed_weather_market_count"),
                "closed_temperature_market_count": closed.get("closed_temperature_market_count"),
                "replay_supported_market_count": closed.get("replay_supported_market_count"),
            },
            "historical_forecasts": {
                "rows": _count_jsonl(args.forecasts),
                "forecast_count": forecast.get("forecast_count"),
                "gap_count": forecast.get("gap_count"),
            },
            "price_history": {
                "rows": _count_jsonl(args.price_history),
                "price_row_count": price_gap.get("price_row_count"),
                "gap_count": price_gap.get("gap_count"),
                "executable_depth_available": False,
            },
            "historical_alpha_dataset": {
                "rows": _count_jsonl(args.dataset),
                "row_count": dataset.get("row_count"),
                "missing_field_counts": dataset.get("missing_field_counts"),
            },
        },
        "web_api_backfill_sources": [
            "aviationweather_metar_history",
            "polymarket_gamma_closed_events",
            "polymarket_clob_prices_history",
            "open_meteo_historical_forecast",
            "kalshi_public_market_reference",
        ],
        "self_sampling_still_required": [
            "pre-resolution executable orderbook depth",
            "slippage and missed-fill measurement",
            "maker quote lifecycle if maker execution is reconsidered",
        ],
        "historical_observation_lock_replay": replay,
        "historical_observation_lock_alpha": obs_alpha,
        "historical_non_dust_threshold_replay": "pending_non_dust_uuww_due_runner",
        "execution_sampling": execution_sampling,
        "blockers": [
            "historical Polymarket prices are not executable orderbook depth",
            "observation-lock needs nonzero executable-price fills before PnL can count",
            "non-dust threshold path still waits for due runner resolution",
        ],
        "next_action": "collect targeted executable orderbook depth only for non-dust ge/le supported-source markets while continuing web/API backfills",
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize web-first paper-only alpha research evidence.")
    parser.add_argument("--metar-history", default="evidence/official_observations/metar_intraday_history.jsonl")
    parser.add_argument("--metar-manifest", default="evidence/official_observations/metar_intraday_history_manifest.json")
    parser.add_argument("--metar-gap-report", default="evidence/official_observations/metar_intraday_gap_report.json")
    parser.add_argument("--closed-markets", default="evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
    parser.add_argument("--closed-manifest", default="evidence/historical_markets/polymarket_closed_weather_manifest.json")
    parser.add_argument("--forecasts", default="evidence/historical_forecasts/open_meteo_forecasts.jsonl")
    parser.add_argument("--forecast-manifest", default="evidence/historical_forecasts/open_meteo_forecast_manifest.json")
    parser.add_argument("--price-history", default="evidence/historical_markets/polymarket_price_history.jsonl")
    parser.add_argument("--price-gap-report", default="evidence/historical_markets/polymarket_price_history_gap_report.json")
    parser.add_argument("--dataset", default="evidence/historical_alpha/weather_alpha_dataset.jsonl")
    parser.add_argument("--dataset-manifest", default="evidence/historical_alpha/weather_alpha_dataset_manifest.json")
    parser.add_argument("--observation-lock-summary", default="evidence/historical_replay/observation_lock_historical_summary.json")
    parser.add_argument("--execution-sampling-manifest", default="evidence/execution_sampling/minimal_orderbook_archive_manifest.json")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_web_first_alpha_research_report(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
