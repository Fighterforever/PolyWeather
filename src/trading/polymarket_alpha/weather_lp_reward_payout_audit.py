from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_payout_audit.v1"
MANUAL_PAYOUT_AUDIT_TEMPLATE_V2_COLUMNS = [
    "audit_id",
    "wallet_or_account_note",
    "date_utc",
    "market_slug",
    "token_id",
    "outcome",
    "quote_price",
    "quote_size",
    "order_start_time_utc",
    "order_cancel_time_utc",
    "time_on_book_minutes",
    "reward_qualified_minutes",
    "expected_reward_low",
    "expected_reward_base",
    "expected_reward_high",
    "actual_reward_received",
    "payout_time_utc",
    "payout_source",
    "tx_hash_or_statement_ref",
    "below_minimum_payout_possible",
    "discrepancy_amount",
    "discrepancy_reason",
    "actual_markout",
    "fill_occurred",
    "adverse_selection_notes",
    "operator_notes",
]


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
        "reward_payout_gap_reason": gap_reason or "none",
        "minimum_payout_threshold": None,
        "minimum_payout_threshold_if_known": None,
        "below_minimum_payout_risk": "unknown_without_wallet_statement",
        "next_audit_time_utc": (audit_dt + timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "next_reward_audit_time_utc": (audit_dt + timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "manual_audit_required": not bool(payout_materialized),
        "quote_count": len(quote_rows),
        "quote_update_count": len(update_rows),
        "reward_allocation_available_count": allocation_available,
        "manual_template_columns": [
            "audit_date_utc",
            "wallet_or_account_note",
            "market_slug",
            "quote_id",
            "token_id",
            "city",
            "strategy_variant",
            "expected_reward_conservative",
            "expected_reward_base",
            "expected_reward_optimistic",
            "expected_reward_visible_proxy",
            "actual_reward_received",
            "payout_time_utc",
            "tx_hash_or_statement_ref",
            "discrepancy_amount",
            "discrepancy_reason",
            "notes",
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
                    "audit_date_utc": report.get("audit_date_utc"),
                    "wallet_or_account_note": "",
                    "market_slug": row.get("market_slug"),
                    "quote_id": row.get("quote_id"),
                    "token_id": row.get("token_id"),
                    "city": row.get("city"),
                    "strategy_variant": row.get("strategy_variant"),
                    "expected_reward_conservative": report.get("expected_reward_scenarios", {}).get("conservative"),
                    "expected_reward_base": report.get("expected_reward_scenarios", {}).get("base"),
                    "expected_reward_optimistic": report.get("expected_reward_scenarios", {}).get("optimistic"),
                    "expected_reward_visible_proxy": report.get("expected_reward_scenarios", {}).get("visible_share_proxy"),
                    "actual_reward_received": "",
                    "payout_time_utc": "",
                    "tx_hash_or_statement_ref": "",
                    "discrepancy_amount": "",
                    "discrepancy_reason": "",
                    "notes": "",
                }
            )
    return len(materialized)


def write_empty_manual_payout_audit_template_v2(path: str | Path) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANUAL_PAYOUT_AUDIT_TEMPLATE_V2_COLUMNS)
        writer.writeheader()
    return 0


def render_manual_payout_template_v2_markdown() -> str:
    lines = [
        "# Weather LP Manual Payout Audit Template v2",
        "",
        "Fill this after a user-run manual UI-only audit. Leave unknown fields blank; do not invent payout.",
        "",
        "## Required Columns",
    ]
    for column in MANUAL_PAYOUT_AUDIT_TEMPLATE_V2_COLUMNS:
        lines.append(f"- {column}")
    lines.extend(["", "live_order_path=false"])
    return "\n".join(lines) + "\n"


def build_manual_payout_audit_result(
    *,
    filled_rows: Iterable[Dict[str, Any]],
    filled_csv_exists: bool,
) -> Dict[str, Any]:
    rows = [row for row in filled_rows if isinstance(row, dict)]
    if not filled_csv_exists:
        return {
            "schema_version": f"{SCHEMA_VERSION}.manual_result.v1",
            "audit_status": "waiting_for_manual_audit",
            "audit_verdict": "insufficient_manual_data",
            "actual_reward_total": None,
            "expected_low": None,
            "expected_base": None,
            "expected_high": None,
            "actual_vs_expected_ratio": None,
            "actual_markout": None,
            "fill_count": 0,
            "adverse_selection_notes": [],
            "next_action": "wait_for_user_manual_audit_result",
            "manual_review_required": True,
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
    actual_reward_total = sum(_safe_float(row.get("actual_reward_received")) or 0.0 for row in rows)
    expected_low = sum(_safe_float(row.get("expected_reward_low")) or 0.0 for row in rows)
    expected_base = sum(_safe_float(row.get("expected_reward_base")) or 0.0 for row in rows)
    expected_high = sum(_safe_float(row.get("expected_reward_high")) or 0.0 for row in rows)
    actual_markout_values = [_safe_float(row.get("actual_markout")) for row in rows]
    actual_markout = sum(value or 0.0 for value in actual_markout_values if value is not None)
    fill_count = sum(1 for row in rows if str(row.get("fill_occurred") or "").strip().lower() in {"1", "true", "yes", "y"})
    notes = [str(row.get("adverse_selection_notes")) for row in rows if row.get("adverse_selection_notes")]
    if not rows:
        verdict = "insufficient_manual_data"
    elif actual_reward_total <= 0:
        verdict = "no_reward_received"
    elif expected_base > 0 and actual_reward_total < expected_low:
        verdict = "reward_below_expected"
    else:
        verdict = "reward_payout_confirmed"
    ratio = actual_reward_total / expected_base if expected_base else None
    next_action = "continue_manual_audit_review" if verdict == "reward_payout_confirmed" else "keep_live_disabled_until_reward_payout_confirmed"
    return {
        "schema_version": f"{SCHEMA_VERSION}.manual_result.v1",
        "audit_status": "manual_audit_ingested",
        "audit_verdict": verdict,
        "actual_reward_total": round(actual_reward_total, 8),
        "expected_low": round(expected_low, 8),
        "expected_base": round(expected_base, 8),
        "expected_high": round(expected_high, 8),
        "actual_vs_expected_ratio": round(ratio, 8) if ratio is not None else None,
        "actual_markout": round(actual_markout, 8),
        "fill_count": fill_count,
        "adverse_selection_notes": notes,
        "next_action": next_action,
        "manual_review_required": True,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def render_manual_template_markdown(report: Dict[str, Any], rows: Iterable[Dict[str, Any]]) -> str:
    materialized = [row for row in rows if isinstance(row, dict)]
    lines = [
        "# Weather LP Reward Payout Manual Audit Template",
        "",
        f"Audit date UTC: {report.get('audit_date_utc')}",
        f"Audit status: {report.get('audit_status')}",
        f"Reward payout gap reason: {report.get('reward_payout_gap_reason')}",
        "",
        "This template is for manual payout reconciliation only. It does not prove payout and does not enable live trading.",
        "",
        "## Expected Reward Scenarios",
    ]
    for key, value in (report.get("expected_reward_scenarios") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Rows To Fill"])
    for row in materialized[:50]:
        lines.append(
            f"- market={row.get('market_slug')} quote_id={row.get('quote_id')} token_id={row.get('token_id')} actual_reward_received=____ tx_hash_or_statement_ref=____"
        )
    lines.extend(["", "live_order_path=false"])
    return "\n".join(lines) + "\n"


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


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def load_csv(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    with source.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


__all__ = [
    "MANUAL_PAYOUT_AUDIT_TEMPLATE_V2_COLUMNS",
    "SCHEMA_VERSION",
    "build_manual_payout_audit_result",
    "build_weather_lp_reward_payout_audit_report",
    "load_csv",
    "load_json",
    "load_jsonl",
    "render_manual_template_markdown",
    "render_manual_payout_template_v2_markdown",
    "write_empty_manual_payout_audit_template_v2",
    "write_json",
    "write_manual_template",
    "write_text",
]
