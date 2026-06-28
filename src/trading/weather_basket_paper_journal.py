from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_paper_journal import stable_json_hash, utc_now_iso


SCHEMA_VERSION = "polyweather_weather_basket_paper_journal.v1"


def _write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]], *, append: bool = True) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with target.open(mode, encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(materialized)


def build_basket_paper_fill(candidate: Dict[str, Any], *, created_at: Optional[str] = None) -> Dict[str, Any]:
    created_at = created_at or utc_now_iso()
    legs = [dict(row) for row in candidate.get("legs") or [] if isinstance(row, dict)]
    identity = {
        "strategy_id": candidate.get("strategy_id"),
        "event_slug": candidate.get("event_slug"),
        "target_date": candidate.get("target_date"),
        "station_code": candidate.get("station_code"),
        "legs": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
                "best_ask": row.get("best_ask"),
            }
            for row in legs
        ],
        "created_at": created_at,
    }
    return {
        "schema_version": "polyweather_weather_basket_paper_fill.v1",
        "basket_fill_id": stable_json_hash(identity, length=24),
        "strategy_id": candidate.get("strategy_id"),
        "event_slug": candidate.get("event_slug"),
        "station_code": candidate.get("station_code"),
        "target_date": candidate.get("target_date"),
        "settlement_source": candidate.get("settlement_source"),
        "legs": legs,
        "total_cost": candidate.get("total_cost"),
        "worst_case_payout": candidate.get("worst_case_payout"),
        "edge_cents": candidate.get("edge_cents"),
        "solver_method": candidate.get("solver_method"),
        "outcome_payoff_vector": candidate.get("outcome_payoff_vector"),
        "created_at": created_at,
        "orderbook_snapshot_ids": [
            row.get("orderbook_snapshot_id")
            for row in legs
            if row.get("orderbook_snapshot_id")
        ],
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def write_basket_paper_fills(
    candidates: Iterable[Dict[str, Any]],
    *,
    paper_fill_dir: str | Path,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    created_at = created_at or utc_now_iso()
    root = Path(paper_fill_dir)
    fills = [build_basket_paper_fill(row, created_at=created_at) for row in candidates if isinstance(row, dict)]
    fills_path = root / "fills.jsonl"
    manifest_path = root / "manifest.jsonl"
    written = _write_jsonl(fills_path, fills, append=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "created_at": created_at,
        "basket_paper_fill_count": len(fills),
        "written_count": written,
        "fills_path": str(fills_path),
    }
    _write_jsonl(manifest_path, [manifest], append=True)
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "fills": fills,
    }


__all__ = ["SCHEMA_VERSION", "build_basket_paper_fill", "write_basket_paper_fills"]
