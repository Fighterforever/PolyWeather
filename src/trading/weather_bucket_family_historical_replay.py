from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_bucket_family import build_weather_bucket_family_catalog
from src.trading.weather_bucket_family_arbitrage import build_bucket_family_arbitrage_report


SCHEMA_VERSION = "polyweather_weather_bucket_family_historical_replay.v1"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side(row: Dict[str, Any]) -> str:
    return str(row.get("side") or row.get("outcome") or "").strip().lower()


def _market_price(row: Dict[str, Any]) -> Optional[float]:
    for field in ("best_ask", "ask", "price", "market_probability"):
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    return None


def _normalize_approximate_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cloned = dict(row)
        if cloned.get("best_ask") is None:
            cloned["best_ask"] = _market_price(cloned)
        if cloned.get("ask_depth_usdc_3c") is None:
            cloned["ask_depth_usdc_3c"] = None
        normalized.append(cloned)
    return normalized


def build_bucket_family_historical_replay(
    rows: Iterable[Dict[str, Any]],
    *,
    min_edge_cents: float = 1.0,
    cost_cents: float = 0.0,
) -> Dict[str, Any]:
    """Build an approximate historical structural-arbitrage replay.

    Closed market rows normally do not include executable historical order book
    depth. This report therefore separates obvious price-sum edge from real PnL.
    """

    normalized_rows = _normalize_approximate_rows(rows)
    catalog = build_weather_bucket_family_catalog(normalized_rows)
    arbitrage = build_bucket_family_arbitrage_report(
        catalog,
        min_edge_cents=min_edge_cents,
        min_leg_depth=0.0,
        cost_cents=cost_cents,
        max_candidates=1000,
    )
    rows_out: List[Dict[str, Any]] = []
    by_station: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"station_code": "", "family_count": 0, "approximate_edge_candidate_count": 0, "approximate_pnl_cents": 0.0})
    by_event_date: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"target_date": "", "family_count": 0, "approximate_edge_candidate_count": 0, "approximate_pnl_cents": 0.0})
    missing_price_or_depth_count = 0
    approximate_edge_candidate_count = 0
    executable_depth_available_count = 0
    approximate_pnl_cents = 0.0

    for row in (arbitrage.get("basket_rows") or []) + (arbitrage.get("monotonic_rows") or []):
        if not isinstance(row, dict):
            continue
        blockers = set(str(item) for item in (row.get("blockers") or []))
        edge = _safe_float(row.get("edge_cents"))
        approximate_candidate = (
            edge is not None
            and edge > float(min_edge_cents)
            and "incomplete_partition" not in blockers
            and "missing_ask_or_token" not in blockers
        )
        executable_depth = approximate_candidate and "missing_depth" not in blockers and "depth_below_min" not in blockers
        if "missing_ask_or_token" in blockers or "missing_depth" in blockers:
            missing_price_or_depth_count += 1
        if approximate_candidate:
            approximate_edge_candidate_count += 1
            approximate_pnl_cents += float(edge or 0.0)
        if executable_depth:
            executable_depth_available_count += 1
        replay_row = {
            "schema_version": "polyweather_weather_bucket_family_historical_replay_row.v1",
            "strategy_id": row.get("strategy_id"),
            "event_slug": row.get("event_slug"),
            "station_code": row.get("station_code"),
            "target_date": row.get("target_date"),
            "bucket_count": row.get("bucket_count"),
            "edge_cents": row.get("edge_cents"),
            "blockers": row.get("blockers") or [],
            "approximate_edge_candidate": approximate_candidate,
            "executable_depth_available": executable_depth,
            "can_count_as_real_pnl": False,
            "approximation_reason": "closed_rows_use_terminal_price_without_historical_executable_depth",
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        rows_out.append(replay_row)
        station = str(row.get("station_code") or "UNKNOWN")
        target_date = str(row.get("target_date") or "UNKNOWN")
        by_station[station]["station_code"] = station
        by_station[station]["family_count"] += 1
        by_event_date[target_date]["target_date"] = target_date
        by_event_date[target_date]["family_count"] += 1
        if approximate_candidate:
            by_station[station]["approximate_edge_candidate_count"] += 1
            by_station[station]["approximate_pnl_cents"] += float(edge or 0.0)
            by_event_date[target_date]["approximate_edge_candidate_count"] += 1
            by_event_date[target_date]["approximate_pnl_cents"] += float(edge or 0.0)

    for bucket in list(by_station.values()) + list(by_event_date.values()):
        bucket["approximate_pnl_cents"] = round(float(bucket.get("approximate_pnl_cents") or 0.0), 6)

    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "can_count_as_real_pnl": False,
        "family_count": catalog.get("family_count", 0),
        "replayable_family_count": catalog.get("partition_candidate_count", 0),
        "approximate_edge_candidate_count": approximate_edge_candidate_count,
        "executable_depth_available_count": executable_depth_available_count,
        "approximate_pnl_cents": round(approximate_pnl_cents, 6),
        "by_station": sorted(by_station.values(), key=lambda item: item["station_code"]),
        "by_event_date": sorted(by_event_date.values(), key=lambda item: item["target_date"]),
        "missing_price_or_depth_count": missing_price_or_depth_count,
        "catalog_summary": {
            "family_count": catalog.get("family_count", 0),
            "partition_candidate_count": catalog.get("partition_candidate_count", 0),
            "gap_counts": catalog.get("gap_counts") or [],
        },
        "arbitrage_summary": {
            "candidate_count": arbitrage.get("candidate_count", 0),
            "best_edge_cents": arbitrage.get("best_edge_cents"),
            "no_candidate_blocker_counts": arbitrage.get("no_candidate_blocker_counts") or [],
        },
        "rows": rows_out,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


__all__ = ["SCHEMA_VERSION", "build_bucket_family_historical_replay", "load_jsonl"]
