from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_payout_audit.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_weather_lp_reward_payout_audit_report(
    *,
    reward_dollarization_report: Dict[str, Any],
    profitability_simulation_report: Dict[str, Any],
    paper_quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
    reward_allocation_audit_report: Dict[str, Any],
    payout_rows: Iterable[Dict[str, Any]] = (),
    generated_at: str | None = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _now_iso()
    scenario = profitability_simulation_report.get("scenario_table") or {}
    exact_reward_available = bool(
        reward_dollarization_report.get("exact_reward_cents_available_count")
        or profitability_simulation_report.get("exact_reward_available")
    )
    payout_materialized = [row for row in payout_rows if isinstance(row, dict)]
    payout_amount = sum(float(row.get("actual_reward_received") or 0.0) for row in payout_materialized) if payout_materialized else None
    audit_dt = _parse_utc(generated_at) or datetime.now(timezone.utc)
    quote_rows = [row for row in paper_quotes if isinstance(row, dict)]
    update_rows = [row for row in quote_updates if isinstance(row, dict)]
    allocation_available = int(reward_allocation_audit_report.get("reward_allocation_available_count") or 0)
    if payout_materialized:
        gap_reason = None
        audit_status = "payout_observed"
    elif not exact_reward_available:
        gap_reason = "exact_total_score_unavailable"
        audit_status = "manual_audit_required"
    else:
        gap_reason = "no_wallet_data"
        audit_status = "manual_audit_required"
    if not update_rows:
        gap_reason = "not_epoch_end_yet"
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_date_utc": generated_at,
        "audit_status": audit_status,
        "expected_reward_scenarios": {
            "conservative": scenario.get("conservative_reward"),
            "base": scenario.get("base_reward"),
            "optimistic": scenario.get("optimistic_reward"),
            "visible_share_proxy": scenario.get("visible_upper_reward"),
        },
        "exact_reward_available": exact_reward_available,
        "payout_observed": bool(payout_materialized),
        "payout_amount": payout_amount,
        "payout_source": "manual_csv" if payout_materialized else None,
        "payout_gap_reason": gap_reason or "none",
        "minimum_payout_threshold": None,
        "next_audit_time_utc": (audit_dt + timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "quote_count": len(quote_rows),
        "quote_update_count": len(update_rows),
        "reward_allocation_available_count": allocation_available,
        "manual_template_columns": [
            "date_utc",
            "wallet",
            "market_slug",
            "expected_reward_low",
            "expected_reward_base",
            "expected_reward_high",
            "actual_reward_received",
            "transaction_hash_or_note",
            "discrepancy_reason",
        ],
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def write_manual_template(path: str | Path, rows: Iterable[Dict[str, Any]], report: Dict[str, Any]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = report.get("manual_template_columns") or []
    materialized = [row for row in rows if isinstance(row, dict)]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in materialized:
            writer.writerow(
                {
                    "date_utc": report.get("audit_date_utc"),
                    "wallet": "",
                    "market_slug": row.get("market_slug"),
                    "expected_reward_low": report.get("expected_reward_scenarios", {}).get("conservative"),
                    "expected_reward_base": report.get("expected_reward_scenarios", {}).get("base"),
                    "expected_reward_high": report.get("expected_reward_scenarios", {}).get("optimistic"),
                    "actual_reward_received": "",
                    "transaction_hash_or_note": "",
                    "discrepancy_reason": "",
                }
            )
    return len(materialized)


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def load_csv(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    with source.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


__all__ = [
    "SCHEMA_VERSION",
    "build_weather_lp_reward_payout_audit_report",
    "load_csv",
    "load_json",
    "load_jsonl",
    "write_json",
    "write_manual_template",
]
