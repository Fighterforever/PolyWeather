#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_observation_lock_signal import build_observation_lock_signal_row  # noqa: E402
from src.trading.weather_paper_journal import _safe_float, load_jsonl, utc_now_iso  # noqa: E402


SCHEMA_VERSION = "polyweather_weather_observation_lock_replay.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _count_by(rows: Iterable[Dict[str, Any]], field: str, *, key_name: str) -> list[dict]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = _text(row.get(field)) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _closed_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = _text(row.get("token_id"))
        slug = _text(row.get("market_slug") or row.get("slug"))
        if token:
            index[f"token:{token}"] = row
        if slug:
            index[f"slug:{slug}"] = row
    return index


def _matching_closed(row: Dict[str, Any], closed: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    token = _text(row.get("token_id"))
    slug = _text(row.get("market_slug"))
    if token and f"token:{token}" in closed:
        return closed[f"token:{token}"]
    if slug and f"slug:{slug}" in closed:
        return closed[f"slug:{slug}"]
    return None


def _expected_yes(closed_row: Dict[str, Any]) -> Optional[bool]:
    value = _safe_float(closed_row.get("official_final_value"))
    spec = closed_row.get("settlement_spec") if isinstance(closed_row.get("settlement_spec"), dict) else {}
    parsed = closed_row.get("parsed_temperature_spec") if isinstance(closed_row.get("parsed_temperature_spec"), dict) else {}
    bucket_type = _text(spec.get("bucket_type") or parsed.get("comparator") or closed_row.get("bucket_type")).lower()
    threshold = _safe_float(spec.get("threshold") or parsed.get("threshold") or closed_row.get("threshold"))
    upper = _safe_float(spec.get("upper_threshold") or parsed.get("upper_threshold") or closed_row.get("upper_threshold"))
    if value is None or threshold is None:
        return None
    rounded = round(value)
    if bucket_type == "ge":
        return rounded >= threshold
    if bucket_type == "le":
        return rounded <= threshold
    if bucket_type == "eq":
        return rounded == threshold
    if bucket_type == "range" and upper is not None:
        return threshold <= rounded <= upper
    return None


def _pnl_cents(signal: Dict[str, Any], closed_row: Dict[str, Any]) -> Optional[float]:
    expected_yes = _expected_yes(closed_row)
    q_effective = _safe_float(signal.get("q_effective"))
    locked_side = _text(signal.get("locked_side")).upper()
    if expected_yes is None or q_effective is None or locked_side not in {"YES", "NO"}:
        return None
    locked_payout = 1.0 if ((locked_side == "YES" and expected_yes) or (locked_side == "NO" and not expected_yes)) else 0.0
    return round((locked_payout - q_effective) * 100.0, 6)


def build_observation_lock_replay_report(
    *,
    orderbook_rows: Iterable[Dict[str, Any]],
    closed_rows: Iterable[Dict[str, Any]],
    observation_rows: Iterable[Dict[str, Any]],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    closed = _closed_index(closed_rows)
    observations = [row for row in observation_rows if isinstance(row, dict)]
    matched_rows: list[dict] = []
    missing_intraday: list[dict] = []
    replay_rows: list[dict] = []
    for row in orderbook_rows:
        if not isinstance(row, dict):
            continue
        closed_row = _matching_closed(row, closed)
        if not closed_row:
            continue
        matched_rows.append(row)
        station = _text(row.get("settlement_station_code") or (row.get("settlement_spec") or {}).get("station_code")).upper()
        target_date = _text(row.get("target_date") or (row.get("settlement_spec") or {}).get("target_date"))
        source = _text(row.get("settlement_source") or (row.get("settlement_spec") or {}).get("settlement_source")).lower()
        scoped_obs = [
            obs
            for obs in observations
            if _text(obs.get("station_code")).upper() == station
            and _text(obs.get("target_date")) == target_date
            and _text(obs.get("source")).lower() == source
        ]
        if not scoped_obs:
            missing_intraday.append(
                {
                    "market_slug": row.get("market_slug"),
                    "token_id": row.get("token_id"),
                    "station_code": station or None,
                    "target_date": target_date or None,
                    "settlement_source": source or None,
                }
            )
            continue
        signal = build_observation_lock_signal_row(
            row,
            observations=scoped_obs,
            generated_at=row.get("recorded_at") or generated_at,
        )
        signal["resolved_pnl_cents"] = _pnl_cents(signal, closed_row) if signal.get("decision") == "candidate" else None
        replay_rows.append(signal)
    candidate_rows = [row for row in replay_rows if row.get("decision") == "candidate"]
    pnl_values = [float(row["resolved_pnl_cents"]) for row in candidate_rows if row.get("resolved_pnl_cents") is not None]
    no_lookahead = bool(observations) and not missing_intraday
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "no_lookahead": no_lookahead,
        "replayable_count": len(replay_rows),
        "missing_intraday_count": len(missing_intraday),
        "missing_intraday_observation_points": missing_intraday[:20],
        "cannot_replay_lock_without_intraday_timeline": bool(missing_intraday),
        "locked_candidate_count": len(candidate_rows),
        "locked_candidate_resolved_pnl_cents": round(sum(pnl_values), 6) if pnl_values else None,
        "by_station": _count_by(replay_rows or missing_intraday, "station_code", key_name="station_code"),
        "by_bucket_type": _count_by(replay_rows, "bucket_type", key_name="bucket_type"),
        "rows": replay_rows,
    }


def _write_json(path: Optional[str | Path], payload: Dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay observation-lock logic against archived weather books.")
    parser.add_argument("--orderbook-archive-dir", default="evidence/orderbook_archive")
    parser.add_argument("--backfill-dir", default="evidence/weather_backfill_local")
    parser.add_argument("--observation-jsonl", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--summary-output", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_observation_lock_replay_report(
        orderbook_rows=load_jsonl(Path(args.orderbook_archive_dir) / "orderbook_snapshots.jsonl"),
        closed_rows=load_jsonl(Path(args.backfill_dir) / "closed_markets.jsonl"),
        observation_rows=load_jsonl(args.observation_jsonl) if args.observation_jsonl else [],
        generated_at=args.generated_at,
    )
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
