from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_closed_market_backfill_bulk import load_closed_weather_markets
from src.trading.weather_paper_journal import load_jsonl
from src.weather.weather_observations import OfficialIntradayObservationRepository
from src.weather.weather_sources import parse_utc, utc_iso


SCHEMA_VERSION = "polyweather_historical_alpha_dataset.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_forecast_by_station_date(rows: Iterable[Dict[str, Any]]) -> Dict[tuple[str, str], Dict[str, Any]]:
    selected: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        station = _text(row.get("station_code")).upper()
        target_date = _text(row.get("target_date"))
        available_at = parse_utc(row.get("available_at"))
        if not station or not target_date or available_at is None:
            continue
        key = (station, target_date)
        old = selected.get(key)
        old_time = parse_utc(old.get("available_at")) if old else None
        if old is None or old_time is None or available_at > old_time:
            selected[key] = row
    return selected


def _latest_price_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        token = _text(row.get("token_id") or row.get("market"))
        timestamp = parse_utc(row.get("timestamp") or row.get("available_at"))
        if not token or timestamp is None:
            continue
        old = selected.get(token)
        old_time = parse_utc(old.get("timestamp") or old.get("available_at")) if old else None
        if old is None or old_time is None or timestamp > old_time:
            selected[token] = row
    return selected


def build_weather_historical_alpha_dataset(
    *,
    closed_markets: Iterable[Dict[str, Any]],
    intraday_repository: OfficialIntradayObservationRepository,
    forecast_rows: Iterable[Dict[str, Any]] = (),
    price_rows: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    forecasts = _latest_forecast_by_station_date(forecast_rows)
    prices = _latest_price_by_token(price_rows)
    rows: List[Dict[str, Any]] = []
    missing_counter: Counter[str] = Counter()
    for market in closed_markets:
        if not isinstance(market, dict):
            continue
        station = _text(market.get("station_code")).upper()
        target_date = _text(market.get("target_date"))
        token = _text(market.get("token_id") or (market.get("token_id_by_outcome") or {}).get("Yes"))
        spec = market.get("settlement_spec") if isinstance(market.get("settlement_spec"), dict) else {}
        close_time = parse_utc(spec.get("market_close_time") or market.get("end_time") or market.get("closed_at"))
        missing_fields: List[str] = []
        if not station:
            missing_fields.append("station_code")
        if not target_date:
            missing_fields.append("target_date")
        if close_time is None:
            missing_fields.append("market_close_time")
        current_high = None
        obs = None
        if station and target_date and close_time is not None:
            obs = intraday_repository.current_high_as_of(
                station_code=station,
                target_date=target_date,
                replay_time=utc_iso(close_time),
                settlement_source=_text(market.get("settlement_source") or "metar").lower(),
            )
            current_high = obs.get("current_high_c")
            if current_high is None:
                missing_fields.append("intraday")
        forecast = forecasts.get((station, target_date))
        if not forecast:
            missing_fields.append("forecast")
        price = prices.get(token)
        if not price:
            missing_fields.append("price")
        outcome = _safe_float(market.get("settled_yes_payout"))
        if outcome is None:
            missing_fields.append("outcome")
        for field in missing_fields:
            missing_counter[field] += 1
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
                "market_slug": market.get("market_slug"),
                "token_id": token or None,
                "station_code": station or None,
                "target_date": target_date or None,
                "bucket_type": market.get("bucket_type") or spec.get("bucket_type"),
                "threshold": market.get("threshold") or spec.get("threshold"),
                "replay_time": utc_iso(close_time) if close_time else None,
                "current_high_as_of_replay": current_high,
                "forecast_features": {
                    "model": forecast.get("model"),
                    "available_at": forecast.get("available_at"),
                    "predicted_daily_high": forecast.get("predicted_daily_high"),
                }
                if forecast
                else None,
                "lock_state": None,
                "model_probability": None,
                "market_price_if_available": price.get("price") if price else None,
                "executable_price_source": (
                    price.get("source") if price and price.get("executable_depth_available") is True else None
                ),
                "outcome": outcome,
                "payout": outcome,
                "no_lookahead": bool(obs.get("no_lookahead")) if isinstance(obs, dict) else False,
                "missing_fields": sorted(set(missing_fields)),
            }
        )
    return {
        "schema_version": f"{SCHEMA_VERSION}.report",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "row_count": len(rows),
        "missing_field_counts": dict(sorted(missing_counter.items())),
        "rows": rows,
    }


def build_weather_historical_alpha_dataset_from_paths(
    *,
    closed_markets_path: str | Path,
    intraday_observations_path: str | Path,
    forecasts_path: str | Path,
    price_history_path: str | Path,
) -> Dict[str, Any]:
    return build_weather_historical_alpha_dataset(
        closed_markets=load_closed_weather_markets(closed_markets_path),
        intraday_repository=OfficialIntradayObservationRepository(intraday_observations_path),
        forecast_rows=load_jsonl(forecasts_path) if Path(forecasts_path).exists() else [],
        price_rows=load_jsonl(price_history_path) if Path(price_history_path).exists() else [],
    )


def write_historical_alpha_dataset_artifacts(
    report: Dict[str, Any],
    *,
    output_path: str | Path,
    manifest_path: str | Path,
) -> Dict[str, Any]:
    rows = [row for row in report.get("rows") or [] if isinstance(row, dict)]
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "schema_version": "polyweather_historical_alpha_dataset_manifest.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "output_path": str(output_path),
        "row_count": len(rows),
        "missing_field_counts": report.get("missing_field_counts") or {},
    }
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest
