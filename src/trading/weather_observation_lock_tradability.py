from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_paper_journal import load_jsonl
from src.weather.weather_sources import parse_utc


SCHEMA_VERSION = "polyweather_observation_lock_tradability.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_bucket(value: Any) -> str:
    price = _safe_float(value)
    if price is None:
        return "price_unknown"
    if price < 0.005:
        return "price_lt_0_005"
    if price < 0.03:
        return "price_0_005_to_0_03"
    return "price_ge_0_03"


def _load_json(path: str | Path) -> Dict[str, Any]:
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _price_by_token(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        token = _text(row.get("token_id"))
        if token:
            grouped[token].append(row)
    for token_rows in grouped.values():
        token_rows.sort(
            key=lambda item: parse_utc(item.get("timestamp") or item.get("recorded_at") or item.get("available_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
    return grouped


def _price_as_of(rows: List[Dict[str, Any]], replay_time: Any) -> Optional[Dict[str, Any]]:
    replay_dt = parse_utc(replay_time)
    if replay_dt is None:
        return None
    visible: List[Tuple[datetime, Dict[str, Any]]] = []
    for row in rows:
        timestamp = parse_utc(row.get("timestamp") or row.get("recorded_at") or row.get("available_at"))
        if timestamp is not None and timestamp <= replay_dt:
            visible.append((timestamp, row))
    if not visible:
        return None
    return max(visible, key=lambda item: item[0])[1]


def _dataset_by_market(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        slug = _text(row.get("market_slug"))
        if slug and slug not in selected:
            selected[slug] = row
    return selected


def _count_by(rows: Iterable[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    counts = Counter(_text(row.get(field)) or "unknown" for row in rows)
    return [
        {field: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _time_to_close_bucket(minutes: Any) -> str:
    value = _safe_float(minutes)
    if value is None:
        return "unknown"
    if value < 30:
        return "lt_30m"
    if value < 120:
        return "30m_to_2h"
    return "gt_2h"


def _is_ge_le_alpha_eligible(row: Dict[str, Any]) -> bool:
    return (
        _text(row.get("bucket_type")).lower() in {"ge", "le"}
        and _text(row.get("locked_side")).upper() in {"YES", "NO"}
        and row.get("price_bucket") != "price_lt_0_005"
    )


def _locked_side_payout(dataset_row: Optional[Dict[str, Any]], locked_side: str) -> Optional[float]:
    yes_payout = _safe_float((dataset_row or {}).get("payout") if dataset_row else None)
    if yes_payout is None:
        return None
    return yes_payout if locked_side.upper() == "YES" else 1.0 - yes_payout


def _approximate_price(signal: Dict[str, Any], price_row: Optional[Dict[str, Any]]) -> Optional[float]:
    yes_price = _safe_float((price_row or {}).get("price") if price_row else None)
    if yes_price is None:
        return None
    locked_side = _text(signal.get("locked_side")).upper()
    if locked_side == "NO":
        return max(0.0, min(1.0, 1.0 - yes_price))
    return yes_price


def build_observation_lock_tradability_report(
    *,
    historical_replay_report: Dict[str, Any],
    price_history_rows: Iterable[Dict[str, Any]],
    alpha_dataset_rows: Iterable[Dict[str, Any]],
    cost: float = 0.005,
) -> Dict[str, Any]:
    signals = [
        row
        for row in historical_replay_report.get("signals") or []
        if isinstance(row, dict) and _text(row.get("locked_side"))
    ]
    prices = _price_by_token(price_history_rows)
    dataset = _dataset_by_market(alpha_dataset_rows)
    triage_rows: List[Dict[str, Any]] = []
    for signal in signals:
        token = _text(signal.get("token_id"))
        price_row = _price_as_of(prices.get(token) or [], signal.get("replay_time")) if token else None
        price_available = price_row is not None
        approximate_price = _approximate_price(signal, price_row)
        executable_depth_available = bool((price_row or {}).get("executable_depth_available") is True)
        approximate_edge = (
            round(1.0 - float(approximate_price) - float(cost), 6)
            if approximate_price is not None
            else None
        )
        dataset_row = dataset.get(_text(signal.get("market_slug")))
        payout = _locked_side_payout(dataset_row, _text(signal.get("locked_side")))
        data_gaps: List[str] = []
        if not price_available:
            data_gaps.append("missing_historical_price")
        if price_available and not executable_depth_available:
            data_gaps.append("historical_price_not_executable_depth")
        if payout is None:
            data_gaps.append("missing_outcome")
        price_bucket = _price_bucket(approximate_price)
        if price_bucket == "price_lt_0_005":
            data_gaps.append("dust_price_not_live_evidence")
        row = {
            "schema_version": f"{SCHEMA_VERSION}.locked_signal",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "market_slug": signal.get("market_slug"),
            "token_id": token or None,
            "station_code": signal.get("station_code"),
            "target_date": signal.get("target_date"),
            "replay_time": signal.get("replay_time"),
            "lock_state": signal.get("lock_state"),
            "locked_side": signal.get("locked_side"),
            "official_current_high": signal.get("official_current_high"),
            "threshold": signal.get("threshold"),
            "bucket_type": signal.get("bucket_type"),
            "time_to_close": signal.get("time_to_close_minutes"),
            "time_to_close_bucket": _time_to_close_bucket(signal.get("time_to_close_minutes")),
            "historical_price_available": price_available,
            "historical_price_source": (price_row or {}).get("source") if price_row else None,
            "historical_price_timestamp": (price_row or {}).get("timestamp") if price_row else None,
            "approximate_price": approximate_price,
            "approximate_edge": approximate_edge,
            "price_bucket": price_bucket,
            "executable_depth_available": executable_depth_available,
            "can_compute_real_pnl": executable_depth_available,
            "can_count_as_pnl": executable_depth_available,
            "evidence_type": "executable_depth" if executable_depth_available else ("approximate_price_only" if price_available else "price_missing"),
            "outcome": (dataset_row or {}).get("outcome") if dataset_row else None,
            "payout": payout,
            "data_gap_reason": sorted(set(data_gaps)),
            "dust_price": price_bucket == "price_lt_0_005",
        }
        triage_rows.append(row)
    triage_rows.sort(
        key=lambda row: (
            row.get("approximate_edge") is not None,
            float(row.get("approximate_edge") or -999.0),
            -float(row.get("time_to_close") or 999999.0),
        ),
        reverse=True,
    )
    for rank, row in enumerate(triage_rows, start=1):
        row["triage_rank"] = rank
    ge_le_rows = [row for row in triage_rows if _is_ge_le_alpha_eligible(row)]
    ge_le_rows.sort(
        key=lambda row: (
            row.get("approximate_edge") is not None,
            float(row.get("approximate_edge") or -999.0),
            -float(row.get("time_to_close") or 999999.0),
        ),
        reverse=True,
    )
    for rank, row in enumerate(ge_le_rows, start=1):
        row["ge_le_triage_rank"] = rank
    price_available_count = len([row for row in triage_rows if row.get("historical_price_available")])
    executable_count = len([row for row in triage_rows if row.get("executable_depth_available")])
    positive = len(
        [
            row
            for row in triage_rows
            if row.get("approximate_edge") is not None and float(row.get("approximate_edge") or 0.0) > 0
        ]
    )
    negative = len(
        [
            row
            for row in triage_rows
            if row.get("approximate_edge") is not None and float(row.get("approximate_edge") or 0.0) <= 0
        ]
    )
    missing = len(triage_rows) - price_available_count
    ge_le_price_available_count = len([row for row in ge_le_rows if row.get("historical_price_available")])
    ge_le_missing_price_count = len(ge_le_rows) - ge_le_price_available_count
    ge_le_positive = len(
        [
            row
            for row in ge_le_rows
            if row.get("approximate_edge") is not None and float(row.get("approximate_edge") or 0.0) > 0
        ]
    )
    summary = {
        "schema_version": f"{SCHEMA_VERSION}.summary",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "locked_signal_count": len(triage_rows),
        "price_available_count": price_available_count,
        "executable_depth_available_count": executable_count,
        "approximate_positive_edge_count": positive,
        "approximate_negative_edge_count": negative,
        "missing_price_count": missing,
        "ge_le_locked_signal_count": len(ge_le_rows),
        "ge_le_price_available_count": ge_le_price_available_count,
        "ge_le_approx_positive_edge_count": ge_le_positive,
        "ge_le_missing_price_count": ge_le_missing_price_count,
        "by_station": _count_by(triage_rows, "station_code"),
        "by_bucket_type": _count_by(triage_rows, "bucket_type"),
        "by_time_to_close": _count_by(triage_rows, "time_to_close_bucket"),
        "top_20_approx_positive_locked_signals": [
            row
            for row in triage_rows
            if row.get("approximate_edge") is not None and float(row.get("approximate_edge") or 0.0) > 0
        ][:20],
        "top_20_missing_price_but_high_value_signals": [
            row
            for row in triage_rows
            if not row.get("historical_price_available")
        ][:20],
        "top_ge_le_approx_positive_locked_signals": [
            row
            for row in ge_le_rows
            if row.get("approximate_edge") is not None and float(row.get("approximate_edge") or 0.0) > 0
        ][:20],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "summary": summary,
        "all_locked_signal_triage": triage_rows,
        "alpha_eligible_ge_le_locked_signal_triage": ge_le_rows,
        "rows": triage_rows,
    }


def build_observation_lock_tradability_report_from_paths(
    *,
    historical_replay_path: str | Path,
    price_history_path: str | Path,
    alpha_dataset_path: str | Path,
    cost: float = 0.005,
) -> Dict[str, Any]:
    return build_observation_lock_tradability_report(
        historical_replay_report=_load_json(historical_replay_path),
        price_history_rows=load_jsonl(price_history_path) if Path(price_history_path).exists() else [],
        alpha_dataset_rows=load_jsonl(alpha_dataset_path) if Path(alpha_dataset_path).exists() else [],
        cost=cost,
    )


def write_observation_lock_tradability_artifacts(
    report: Dict[str, Any],
    *,
    summary_output: str | Path,
    rows_output: str | Path,
    ge_le_summary_output: str | Path | None = None,
) -> Dict[str, Any]:
    summary_path = Path(summary_output)
    rows_path = Path(rows_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in report.get("rows") or []:
            if isinstance(row, dict):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    ge_le_output = None
    if ge_le_summary_output:
        ge_le_path = Path(ge_le_summary_output)
        ge_le_path.parent.mkdir(parents=True, exist_ok=True)
        ge_le_report = {
            "schema_version": f"{SCHEMA_VERSION}.ge_le_alpha",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
            "summary": report.get("summary") or {},
            "rows": report.get("alpha_eligible_ge_le_locked_signal_triage") or [],
        }
        ge_le_path.write_text(json.dumps(ge_le_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        ge_le_output = str(ge_le_path)
    return {
        "summary_output": str(summary_path),
        "rows_output": str(rows_path),
        "ge_le_summary_output": ge_le_output,
        "locked_signal_count": (report.get("summary") or {}).get("locked_signal_count"),
    }
