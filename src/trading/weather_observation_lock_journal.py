from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.weather_paper_journal import _append_jsonl, stable_json_hash, utc_now_iso


SCHEMA_VERSION = "polyweather_observation_lock_paper_journal.v1"
FILL_SCHEMA_VERSION = "polyweather_observation_lock_paper_fill.v1"
DEFAULT_OBSERVATION_LOCK_JOURNAL_DIR = Path("evidence/observation_lock_paper")


def _eligible_candidate(row: Dict[str, Any]) -> bool:
    return (
        row.get("decision") == "candidate"
        and str(row.get("bucket_type") or "").lower() in {"ge", "le"}
        and str(row.get("price_bucket") or "") != "price_lt_0_005"
        and row.get("locked_side") in {"YES", "NO"}
    )


def _fill_from_signal(row: Dict[str, Any], *, recorded_at: str) -> Dict[str, Any]:
    identity = {
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "locked_side": row.get("locked_side"),
        "latest_available_at": row.get("latest_available_at"),
    }
    return {
        "schema_version": FILL_SCHEMA_VERSION,
        "fill_id": stable_json_hash(identity, length=24),
        "strategy_id": "observation_lock",
        "recorded_at": recorded_at,
        "market_slug": row.get("market_slug"),
        "token_id": row.get("token_id"),
        "side": row.get("side"),
        "locked_side": row.get("locked_side"),
        "lock_state": row.get("lock_state"),
        "bucket_type": row.get("bucket_type"),
        "station_code": row.get("station_code"),
        "target_date": row.get("target_date"),
        "official_current_high": row.get("official_current_high"),
        "latest_observation_at": row.get("latest_observation_at"),
        "latest_available_at": row.get("latest_available_at"),
        "q_effective": row.get("q_effective"),
        "entry_price": row.get("q_effective"),
        "executable_edge": row.get("executable_edge"),
        "price_bucket": row.get("price_bucket"),
        "orderbook_snapshot_id": row.get("orderbook_snapshot_id") or row.get("snapshot_id"),
        "settlement_spec": row.get("settlement_spec"),
        "no_lookahead": bool(row.get("no_lookahead") is not False),
        "paper_only": True,
        "counts_for_live_gate": False,
    }


def write_observation_lock_paper_journal(
    signal_report: Dict[str, Any],
    *,
    journal_dir: str | Path = DEFAULT_OBSERVATION_LOCK_JOURNAL_DIR,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    recorded_at = recorded_at or utc_now_iso()
    root = Path(journal_dir)
    rows = signal_report.get("rows") if isinstance(signal_report.get("rows"), list) else []
    fills = [_fill_from_signal(row, recorded_at=recorded_at) for row in rows if isinstance(row, dict) and _eligible_candidate(row)]
    written = _append_jsonl(root / "fills.jsonl", fills)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "recorded_at": recorded_at,
        "journal_dir": str(root),
        "fills_path": str(root / "fills.jsonl"),
        "markouts_path": str(root / "markouts.jsonl"),
        "fill_count": len(fills),
        "written_count": written,
        "shadow_or_diagnostic_count": len(
            [
                row
                for row in rows
                if isinstance(row, dict)
                and row.get("decision") in {"shadow", "watch"}
                and not _eligible_candidate(row)
            ]
        ),
    }
    _append_jsonl(root / "manifest.jsonl", [manifest])
    (root / "markouts.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "markouts.jsonl").touch(exist_ok=True)
    return manifest
