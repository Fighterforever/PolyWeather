#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_closed_market_backfill_bulk import load_closed_weather_markets  # noqa: E402
from src.trading.weather_observation_lock_signal import build_observation_lock_signal_row  # noqa: E402
from src.trading.weather_paper_journal import load_jsonl  # noqa: E402
from src.weather.weather_observations import OfficialIntradayObservationRepository  # noqa: E402
from src.weather.weather_sources import parse_utc, utc_iso  # noqa: E402


SCHEMA_VERSION = "polyweather_observation_lock_historical_replay.v1"
DEFAULT_CLOSED_MARKETS = Path("evidence/historical_markets/polymarket_closed_weather_markets.jsonl")
DEFAULT_OBSERVATIONS = Path("evidence/official_observations/metar_intraday_history.jsonl")
DEFAULT_PRICE_HISTORY = Path("evidence/historical_markets/polymarket_price_history.jsonl")
DEFAULT_REPORT = Path("evidence/historical_replay/observation_lock_historical_replay.json")
DEFAULT_FILLS = Path("evidence/historical_replay/observation_lock_historical_fills.jsonl")
DEFAULT_SUMMARY = Path("evidence/historical_replay/observation_lock_historical_summary.json")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count_by(rows: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = _text(row.get(field)) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return [
        {key_name or field: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _load_price_history(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    return load_jsonl(source)


def _price_rows_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        token = _text(row.get("token_id") or row.get("market"))
        if token:
            grouped[token].append(row)
    for token_rows in grouped.values():
        token_rows.sort(key=lambda row: parse_utc(row.get("timestamp") or row.get("recorded_at") or row.get("available_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return grouped


def _price_as_of(rows: List[Dict[str, Any]], replay_time: datetime) -> Optional[Dict[str, Any]]:
    visible: List[Tuple[datetime, Dict[str, Any]]] = []
    for row in rows:
        timestamp = parse_utc(row.get("timestamp") or row.get("recorded_at") or row.get("available_at"))
        if timestamp is not None and timestamp <= replay_time:
            visible.append((timestamp, row))
    if not visible:
        return None
    return max(visible, key=lambda item: item[0])[1]


def _market_close(record: Dict[str, Any]) -> Optional[datetime]:
    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    for value in (spec.get("market_close_time"), spec.get("end_time"), record.get("end_time"), record.get("closed_at")):
        parsed = parse_utc(value)
        if parsed is not None:
            return parsed
    return None


def _record_to_signal_row(record: Dict[str, Any], price_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    spec = record.get("settlement_spec") if isinstance(record.get("settlement_spec"), dict) else {}
    token_map = record.get("token_id_by_outcome") if isinstance(record.get("token_id_by_outcome"), dict) else {}
    yes_token = _text(token_map.get("Yes") or token_map.get("yes") or record.get("token_id"))
    best_ask = _safe_float((price_row or {}).get("best_ask") or (price_row or {}).get("price"))
    best_bid = _safe_float((price_row or {}).get("best_bid"))
    if best_bid is None and best_ask is not None:
        best_bid = max(0.0, best_ask - 0.02)
    spread = _safe_float((price_row or {}).get("spread"))
    if spread is None and best_bid is not None and best_ask is not None:
        spread = round(max(0.0, best_ask - best_bid), 6)
    return {
        "market_slug": record.get("market_slug"),
        "token_id": yes_token or None,
        "side": "YES",
        "outcome": "Yes",
        "bucket_type": record.get("bucket_type") or spec.get("bucket_type"),
        "threshold": record.get("threshold") or spec.get("threshold"),
        "upper_threshold": record.get("upper_threshold") or spec.get("upper_threshold"),
        "station_code": record.get("station_code") or spec.get("station_code"),
        "settlement_source": record.get("settlement_source") or spec.get("settlement_source"),
        "target_date": record.get("target_date") or spec.get("target_date"),
        "market_close_time": spec.get("market_close_time") or record.get("end_time"),
        "settlement_spec": spec,
        "market_bucket": {
            "bucket_type": record.get("bucket_type") or spec.get("bucket_type"),
            "threshold": record.get("threshold") or spec.get("threshold"),
            "upper_threshold": record.get("upper_threshold") or spec.get("upper_threshold"),
        },
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
        "ask_depth_usdc_3c": _safe_float((price_row or {}).get("ask_depth_usdc_3c")),
        "bid_depth_usdc_3c": _safe_float((price_row or {}).get("bid_depth_usdc_3c")),
        "orderbook_snapshot_id": (price_row or {}).get("orderbook_snapshot_id"),
    }


def _candidate_payout(record: Dict[str, Any], locked_side: str) -> Optional[float]:
    settled_yes = _safe_float(record.get("settled_yes_payout"))
    if settled_yes is None:
        probabilities = record.get("settled_probability_by_outcome") if isinstance(record.get("settled_probability_by_outcome"), dict) else {}
        settled_yes = _safe_float(probabilities.get("Yes") if "Yes" in probabilities else probabilities.get("yes"))
    if settled_yes is None:
        return None
    return settled_yes if locked_side.upper() == "YES" else 1.0 - settled_yes


def _score_fills(fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    resolved = [row for row in fills if row.get("payout") is not None and row.get("entry_price") is not None]
    if not resolved:
        return {"resolved_fill_count": 0, "resolved_pnl_cents": None, "brier_score": None, "log_loss": None}
    pnl = sum((float(row["payout"]) - float(row["entry_price"])) * 100.0 for row in resolved)
    brier = sum((float(row.get("locked_probability") or 1.0) - float(row["payout"])) ** 2 for row in resolved) / len(resolved)
    losses = []
    for row in resolved:
        p = min(0.999999, max(0.000001, float(row.get("locked_probability") or 1.0)))
        outcome = float(row["payout"])
        losses.append(-(outcome * math.log(p) + (1.0 - outcome) * math.log(1.0 - p)))
    return {
        "resolved_fill_count": len(resolved),
        "resolved_pnl_cents": round(pnl, 6),
        "brier_score": round(brier, 6),
        "log_loss": round(sum(losses) / len(losses), 6),
    }


def _avg_pnl(rows: List[Dict[str, Any]]) -> Optional[float]:
    scored = _score_fills(rows)
    return scored.get("resolved_pnl_cents")


def _pnl_by(rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_text(row.get(field)) or "unknown"].append(row)
    return [
        {field: key, "fill_count": len(group), **_score_fills(group)}
        for key, group in sorted(grouped.items())
    ]


def build_observation_lock_historical_replay(
    *,
    closed_markets: Iterable[Dict[str, Any]],
    intraday_repository: OfficialIntradayObservationRepository,
    price_history_rows: Iterable[Dict[str, Any]] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_iso(datetime.now(timezone.utc))
    records = [
        record
        for record in closed_markets
        if isinstance(record, dict)
        and str(record.get("market_family") or "").lower() == "temperature"
        and str(record.get("settlement_source") or "").lower() == "metar"
        and record.get("station_code")
        and record.get("target_date")
    ]
    prices_by_token = _price_rows_by_token(price_history_rows)
    signals: List[Dict[str, Any]] = []
    fills: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    no_lookahead_violations = 0
    for record in records:
        close_dt = _market_close(record)
        if close_dt is None:
            gaps.append({"market_slug": record.get("market_slug"), "gap_reason": "missing_market_close_time"})
            continue
        station = _text(record.get("station_code")).upper()
        target_date = _text(record.get("target_date"))
        source = _text(record.get("settlement_source")).lower()
        visible_rows = intraday_repository.query(
            station_code=station,
            target_date=target_date,
            replay_time=utc_iso(close_dt),
            settlement_source=source,
        )
        checkpoints = sorted(
            {
                str(row.get("available_at"))
                for row in visible_rows
                if row.get("available_at") and parse_utc(row.get("available_at")) is not None and parse_utc(row.get("available_at")) <= close_dt
            }
        )
        near_close = close_dt - timedelta(minutes=15)
        if near_close > datetime.min.replace(tzinfo=timezone.utc):
            checkpoints.append(utc_iso(near_close))
        if not checkpoints:
            gaps.append(
                {
                    "market_slug": record.get("market_slug"),
                    "station_code": station,
                    "target_date": target_date,
                    "gap_reason": "missing_intraday_before_market_close",
                }
            )
            continue
        seen_checkpoints: set[str] = set()
        token = _text(record.get("token_id") or (record.get("token_id_by_outcome") or {}).get("Yes"))
        price_rows = prices_by_token.get(token) or []
        for replay_time in sorted(checkpoints):
            if replay_time in seen_checkpoints:
                continue
            seen_checkpoints.add(replay_time)
            replay_dt = parse_utc(replay_time)
            if replay_dt is None or replay_dt > close_dt:
                continue
            price_row = _price_as_of(price_rows, replay_dt) if price_rows else None
            executable_depth = bool(price_row and price_row.get("executable_depth_available") is True)
            row = build_observation_lock_signal_row(
                _record_to_signal_row(record, price_row if executable_depth else None),
                observations=[],
                intraday_repository=intraday_repository,
                generated_at=replay_time,
                min_executable_edge=0.0,
                max_spread=0.03,
                min_ask_depth=1.0,
            )
            row.update(
                {
                    "source": "observation_lock_historical_replay",
                    "market_slug": record.get("market_slug"),
                    "replay_time": replay_time,
                    "market_close_time": utc_iso(close_dt),
                    "time_to_close_minutes": round((close_dt - replay_dt).total_seconds() / 60.0, 3),
                    "historical_executable_price_available": executable_depth,
                    "missing_historical_executable_price": not executable_depth,
                }
            )
            if row.get("no_lookahead") is not True:
                no_lookahead_violations += 1
            signals.append(row)
            if row.get("locked_side") and executable_depth:
                payout = _candidate_payout(record, str(row.get("locked_side")))
                fill = {
                    "schema_version": "polyweather_observation_lock_historical_fill.v1",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                    "market_slug": record.get("market_slug"),
                    "token_id": token,
                    "station_code": station,
                    "target_date": target_date,
                    "bucket_type": row.get("bucket_type"),
                    "replay_time": replay_time,
                    "locked_side": row.get("locked_side"),
                    "locked_probability": row.get("locked_probability"),
                    "entry_price": row.get("q_effective"),
                    "price_bucket": row.get("price_bucket"),
                    "payout": payout,
                    "no_lookahead": row.get("no_lookahead"),
                    "diagnostic_only": True,
                    "counts_for_live_gate": False,
                }
                fills.append(fill)
    locked = [row for row in signals if row.get("locked_side")]
    candidates = [row for row in signals if row.get("locked_side") and row.get("historical_executable_price_available")]
    scored = _score_fills(fills)
    missing_price_count = len([row for row in locked if row.get("missing_historical_executable_price")])
    missing_intraday_signal_count = len(
        [row for row in signals if row.get("lock_state") == "missing_intraday_observation"]
    )
    summary = {
        "schema_version": f"{SCHEMA_VERSION}.summary",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "replay_market_count": len(records),
        "replay_signal_count": len(signals),
        "locked_signal_count": len(locked),
        "candidate_count": len(candidates),
        "fill_count": len(fills),
        "missing_intraday_count": missing_intraday_signal_count,
        "missing_historical_price_count": missing_price_count,
        "no_lookahead_violation_count": no_lookahead_violations,
        "missing_historical_executable_price": missing_price_count > 0,
        **scored,
        "by_station": _count_by(signals, "station_code"),
        "by_bucket_type": _count_by(signals, "bucket_type"),
        "by_price_bucket": _pnl_by(fills, "price_bucket"),
        "by_time_to_close": _count_by(
            [
                {
                    **row,
                    "time_to_close_bucket": (
                        "lt_30m"
                        if float(row.get("time_to_close_minutes") or 0) < 30
                        else "30m_to_2h"
                        if float(row.get("time_to_close_minutes") or 0) < 120
                        else "gt_2h"
                    ),
                }
                for row in signals
            ],
            "time_to_close_bucket",
        ),
        "gap_count": len(gaps),
        "gap_samples": gaps[:20],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "summary": summary,
        "signals": signals,
        "fills": fills,
    }


def _write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [dict(row) for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(materialized)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay paper-only observation-lock alpha on closed markets and METAR timeline.")
    parser.add_argument("--closed-markets", default=str(DEFAULT_CLOSED_MARKETS))
    parser.add_argument("--intraday-observations", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--price-history", default=str(DEFAULT_PRICE_HISTORY))
    parser.add_argument("--summary-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--fills-output", default=str(DEFAULT_FILLS))
    parser.add_argument("--summary-compact-output", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--generated-at", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_observation_lock_historical_replay(
        closed_markets=load_closed_weather_markets(args.closed_markets),
        intraday_repository=OfficialIntradayObservationRepository(args.intraday_observations),
        price_history_rows=_load_price_history(args.price_history),
        generated_at=args.generated_at,
    )
    output_path = Path(args.summary_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_jsonl(args.fills_output, report.get("fills") or [])
    compact = dict(report.get("summary") or {})
    Path(args.summary_compact_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_compact_output).write_text(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
