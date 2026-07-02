from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_readonly import (
    PolymarketReadonlyClient,
    PolymarketReadonlyError,
    classify_weather_market_family_from_row,
)


PAPER_JOURNAL_SCHEMA_VERSION = "polyweather_weather_paper_journal.v1"
PAPER_FILL_SCHEMA_VERSION = "polyweather_weather_paper_fill.v1"
PAPER_MARKOUT_SCHEMA_VERSION = "polyweather_weather_paper_markout.v1"
DEFAULT_PAPER_JOURNAL_DIR = Path("data/trading/weather_paper")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> str:
    return str(value)


def stable_json_hash(value: Any, *, length: int = 16) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[: max(1, int(length))]


def _append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    if not materialized:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=_json_default))
            handle.write("\n")
    return len(materialized)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc_iso(value: Any) -> Optional[datetime]:
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


def _markout_age_seconds(entry_recorded_at: Any, markout_recorded_at: Any) -> Optional[int]:
    entry_dt = _parse_utc_iso(entry_recorded_at)
    markout_dt = _parse_utc_iso(markout_recorded_at)
    if entry_dt is None or markout_dt is None:
        return None
    return max(0, int((markout_dt - entry_dt).total_seconds()))


def markout_horizon_label(age_seconds: Optional[int]) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds < 5 * 60:
        return "0-5m"
    if age_seconds < 15 * 60:
        return "5-15m"
    if age_seconds < 30 * 60:
        return "15-30m"
    if age_seconds < 60 * 60:
        return "30-60m"
    if age_seconds < 4 * 60 * 60:
        return "1-4h"
    if age_seconds < 24 * 60 * 60:
        return "4-24h"
    return "24h+"


def _latest_records_by_fill_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        fill_id = str(record.get("fill_id") or f"row:{index}")
        latest[fill_id] = record
    return latest


def _bucket_label_type(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(">="):
        return "ge"
    if text.startswith("<="):
        return "le"
    if text.startswith("="):
        return "eq"
    if "-" in text:
        return "range"
    return "unknown"


def _numeric_bucket(value: Any, buckets: List[Tuple[float, str]], *, missing: str = "missing") -> str:
    number = _safe_float(value)
    if number is None:
        return missing
    for ceiling, label in buckets:
        if number <= ceiling:
            return label
    return buckets[-1][1] if buckets else str(number)


def price_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "missing"
    if number < 0.03:
        return "<0.03"
    if number < 0.10:
        return "0.03-0.10"
    if number < 0.30:
        return "0.10-0.30"
    if number < 0.60:
        return "0.30-0.60"
    return ">=0.60"


def spread_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "missing"
    if number <= 0.005:
        return "<=0.005"
    if number <= 0.015:
        return "0.005-0.015"
    if number <= 0.03:
        return "0.015-0.03"
    return ">0.03"


def edge_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "missing"
    if number < 10:
        return "<10"
    if number < 20:
        return "10-20"
    if number < 30:
        return "20-30"
    return ">=30"


def _market_family_from_record(record: Dict[str, Any]) -> str:
    explicit = str(record.get("market_family") or "").strip().lower()
    if explicit and explicit != "unknown":
        return explicit
    if not any(
        str(record.get(field) or "").strip()
        for field in ("event_title", "event_slug", "question", "market_slug", "slug", "description")
    ):
        return "unknown"
    return classify_weather_market_family_from_row(record)


def build_paper_run_id(report: Dict[str, Any]) -> str:
    return stable_json_hash(
        {
            "schema_version": report.get("schema_version"),
            "generated_at": report.get("generated_at"),
            "source_snapshot_id": report.get("source_snapshot_id"),
            "summary": report.get("summary"),
        },
        length=20,
    )


def build_paper_fill_records(
    report: Dict[str, Any],
    *,
    run_id: Optional[str] = None,
    profile: str = "default",
    include_candidates: bool = True,
    include_watch: bool = False,
    include_quarantine: bool = False,
    max_records: Optional[int] = None,
    recorded_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    run_id = run_id or build_paper_run_id(report)
    recorded_at = recorded_at or utc_now_iso()
    items: List[Dict[str, Any]] = []
    for key in ("candidates", "watch", "quarantine"):
        if key == "candidates" and not include_candidates:
            continue
        if key == "watch" and not include_watch:
            continue
        if key == "quarantine" and not include_quarantine:
            continue
        for item in report.get(key) or []:
            if not isinstance(item, dict):
                continue
            items.append({**item, "paper_signal_bucket": key})
    if max_records is not None:
        items = items[: max(0, int(max_records))]

    records: List[Dict[str, Any]] = []
    for item in items:
        entry_price = _safe_float(item.get("price"))
        market_family = _market_family_from_record(item)
        fill_identity = {
            "run_id": run_id,
            "source_snapshot_id": report.get("source_snapshot_id"),
            "row_id": item.get("row_id"),
            "market_slug": item.get("market_slug"),
            "token_id": item.get("token_id"),
            "side": item.get("side"),
            "entry_price": entry_price,
            "paper_signal_bucket": item.get("paper_signal_bucket"),
        }
        fill_id = stable_json_hash(fill_identity, length=24)
        records.append(
            {
                "schema_version": PAPER_FILL_SCHEMA_VERSION,
                "fill_id": fill_id,
                "run_id": run_id,
                "recorded_at": recorded_at,
                "paper_only": True,
                "status": "open",
                "profile": str(profile or "default"),
                "source_snapshot_id": report.get("source_snapshot_id"),
                "source_status": report.get("source_status"),
                "source": report.get("source"),
                "market_family": market_family,
                "signal_bucket": item.get("paper_signal_bucket"),
                "decision": item.get("decision"),
                "row_id": item.get("row_id"),
                "city": item.get("city"),
                "event_title": item.get("event_title"),
                "question": item.get("question"),
                "market_id": item.get("market_id"),
                "market_slug": item.get("market_slug"),
                "token_id": item.get("token_id"),
                "side": item.get("side"),
                "outcome": item.get("outcome"),
                "bucket_label": item.get("bucket_label"),
                "bucket_type": item.get("bucket_type"),
                "strategy_id": item.get("strategy_id"),
                "execution_style": item.get("execution_style"),
                "why_now": item.get("why_now"),
                "strategy_live_eligible": bool(item.get("strategy_live_eligible", True)),
                "risk_caps": item.get("risk_caps") if isinstance(item.get("risk_caps"), dict) else None,
                "entry_price": entry_price,
                "entry_bid": _safe_float(item.get("bid")),
                "entry_ask": _safe_float(item.get("ask")),
                "entry_spread": _safe_float(item.get("spread")),
                "entry_liquidity": _safe_float(item.get("liquidity")),
                "entry_bid_depth_usdc_3c": _safe_float(item.get("bid_depth_usdc_3c")),
                "entry_ask_depth_usdc_3c": _safe_float(item.get("ask_depth_usdc_3c")),
                "edge_percent": _safe_float(item.get("edge_percent")),
                "p_lcb": _safe_float(item.get("p_lcb")),
                "q_effective": _safe_float(item.get("q_effective")),
                "cost": _safe_float(item.get("cost")),
                "ev_safe": _safe_float(item.get("ev_safe")),
                "model_probability": _safe_float(item.get("model_probability")),
                "market_probability": _safe_float(item.get("market_probability")),
                "market_implied_yes_price": _safe_float(item.get("market_implied_yes_price")),
                "market_implied_side_price": _safe_float(item.get("market_implied_side_price")),
                "market_implied_de_vig_yes_probability": _safe_float(
                    item.get("market_implied_de_vig_yes_probability")
                ),
                "market_implied_de_vig_side_probability": _safe_float(
                    item.get("market_implied_de_vig_side_probability")
                ),
                "market_implied_cdf": _safe_float(item.get("market_implied_cdf")),
                "market_implied_cdf_raw": _safe_float(item.get("market_implied_cdf_raw")),
                "market_implied_bucket_family": item.get("market_implied_bucket_family"),
                "market_implied_de_vig_status": item.get("market_implied_de_vig_status"),
                "score": _safe_float(item.get("score")),
                "source_final_score": _safe_float(item.get("source_final_score")),
                "target_date": item.get("target_date"),
                "end_time": item.get("end_time"),
                "end_date": item.get("end_date"),
                "settlement_spec_status": item.get("settlement_spec_status"),
                "settlement_rule_hash": item.get("settlement_rule_hash"),
                "settlement_rule_text": item.get("settlement_rule_text"),
                "settlement_station_code": item.get("settlement_station_code"),
                "settlement_station_label": item.get("settlement_station_label"),
                "settlement_source": item.get("settlement_source"),
                "settlement_timezone": item.get("settlement_timezone"),
                "settlement_metric": item.get("settlement_metric"),
                "settlement_unit": item.get("settlement_unit"),
                "settlement_spec": item.get("settlement_spec")
                if isinstance(item.get("settlement_spec"), dict)
                else None,
                "blockers": item.get("blockers") or [],
                "warnings": item.get("warnings") or [],
                "risk_rule_hits": item.get("risk_rule_hits") or [],
                "targeted_shadow": bool(item.get("targeted_shadow")),
                "targeted_shadow_reasons": item.get("targeted_shadow_reasons") or [],
                "original_decision": item.get("original_decision"),
                "quarantine_reason": item.get("quarantine_reason"),
                "quarantine_blocker_scope": item.get("quarantine_blocker_scope"),
                "quarantine_non_risk_blockers": item.get("quarantine_non_risk_blockers") or [],
                "quarantine_non_risk_blocker_categories": item.get("quarantine_non_risk_blocker_categories") or [],
                "would_be_decision_without_risk_rules": item.get("would_be_decision_without_risk_rules"),
                "would_be_decision_without_quarantine_blockers": item.get(
                    "would_be_decision_without_quarantine_blockers"
                ),
                "price_conditioned_status": item.get("price_conditioned_status"),
                "base_rate_reference": item.get("base_rate_reference"),
                "base_rate_note": item.get("base_rate_note"),
                "base_rate_haircut": _safe_float(item.get("base_rate_haircut")),
                "conservative_base_rate_fair_price": _safe_float(
                    item.get("conservative_base_rate_fair_price")
                ),
                "price_discount_cents": _safe_float(item.get("price_discount_cents")),
                "paper_backfill_from_snapshot": bool(
                    item.get("paper_backfill_from_snapshot")
                    or report.get("paper_backfill_from_snapshot")
                ),
                "backfill_source_snapshot_path": item.get("backfill_source_snapshot_path"),
                "backfill_source_run_id": item.get("backfill_source_run_id"),
                "backfill_source_recorded_at": item.get("backfill_source_recorded_at"),
                "live_gate_excluded": bool(item.get("live_gate_excluded")),
                "counts_for_live_gate": bool(item.get("counts_for_live_gate", True)),
                "live_gate_at_record": bool((report.get("summary") or {}).get("live_gate")),
                "live_authorization_pct_at_record": (report.get("summary") or {}).get("live_authorization_pct"),
            }
        )
    return records


def _paper_fill_dedupe_key(record: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(record.get("market_slug") or record.get("market_id") or "").strip(),
        str(record.get("token_id") or "").strip(),
        str(record.get("side") or "").strip().lower(),
    )


def _filter_duplicate_open_fills(
    records: Iterable[Dict[str, Any]],
    *,
    existing_fills: Iterable[Dict[str, Any]],
    min_reentry_seconds: Optional[int] = None,
    recorded_at: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    latest_open_by_key: Dict[Tuple[str, str, str], Optional[datetime]] = {}
    for row in existing_fills:
        if not isinstance(row, dict) or row.get("status") != "open":
            continue
        key = _paper_fill_dedupe_key(row)
        recorded = _parse_utc_iso(row.get("recorded_at"))
        current = latest_open_by_key.get(key)
        if current is None or (recorded is not None and recorded > current):
            latest_open_by_key[key] = recorded
    now_dt = _parse_utc_iso(recorded_at)
    filtered: List[Dict[str, Any]] = []
    duplicate_count = 0
    seen_new = set()
    for record in records:
        key = _paper_fill_dedupe_key(record)
        existing_recorded_at = latest_open_by_key.get(key)
        is_existing_duplicate = key in latest_open_by_key
        if min_reentry_seconds is not None and is_existing_duplicate:
            if now_dt is None or existing_recorded_at is None:
                is_existing_duplicate = True
            else:
                age_seconds = (now_dt - existing_recorded_at).total_seconds()
                is_existing_duplicate = age_seconds < max(0, int(min_reentry_seconds))
        if is_existing_duplicate or key in seen_new:
            duplicate_count += 1
            continue
        filtered.append(record)
        seen_new.add(key)
    return filtered, duplicate_count


def write_paper_journal(
    report: Dict[str, Any],
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    profile: str = "default",
    include_candidates: bool = True,
    include_watch: bool = False,
    include_quarantine: bool = False,
    max_fills: Optional[int] = None,
    recorded_at: Optional[str] = None,
    min_reentry_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    recorded_at = recorded_at or utc_now_iso()
    base_run_id = build_paper_run_id(report)
    run_id = (
        stable_json_hash(
            {
                "base_run_id": base_run_id,
                "recorded_at": recorded_at,
                "min_reentry_seconds": min_reentry_seconds,
            },
            length=20,
        )
        if min_reentry_seconds is not None
        else base_run_id
    )
    snapshot_path = journal_root / "snapshots" / f"{run_id}.json"
    fills_path = journal_root / "paper_fills.jsonl"
    manifest_path = journal_root / "manifest.jsonl"

    snapshot_payload = {
        "schema_version": PAPER_JOURNAL_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_at": recorded_at,
        "profile": str(profile or "default"),
        "report": report,
    }
    _write_json_atomic(snapshot_path, snapshot_payload)
    fill_records = build_paper_fill_records(
        report,
        run_id=run_id,
        profile=profile,
        include_candidates=include_candidates,
        include_watch=include_watch,
        include_quarantine=include_quarantine,
        max_records=max_fills,
        recorded_at=recorded_at,
    )
    fill_records, duplicate_skipped_count = _filter_duplicate_open_fills(
        fill_records,
        existing_fills=load_jsonl(fills_path),
        min_reentry_seconds=min_reentry_seconds,
        recorded_at=recorded_at,
    )
    fill_count = _append_jsonl(fills_path, fill_records)
    manifest_record = {
        "schema_version": PAPER_JOURNAL_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_at": recorded_at,
        "profile": str(profile or "default"),
        "snapshot_path": str(snapshot_path),
        "fills_path": str(fills_path),
        "fill_count": fill_count,
        "duplicate_skipped_count": duplicate_skipped_count,
        "candidate_count": int((report.get("summary") or {}).get("candidate_count") or 0),
        "watch_count": int((report.get("summary") or {}).get("watch_count") or 0),
        "quarantine_count": int((report.get("summary") or {}).get("quarantine_count") or 0),
        "include_candidates": bool(include_candidates),
        "include_quarantine": bool(include_quarantine),
        "min_reentry_seconds": min_reentry_seconds,
        "live_gate": bool((report.get("summary") or {}).get("live_gate")),
        "source_snapshot_id": report.get("source_snapshot_id"),
    }
    _append_jsonl(manifest_path, [manifest_record])
    return {
        "schema_version": PAPER_JOURNAL_SCHEMA_VERSION,
        "run_id": run_id,
        "recorded_at": recorded_at,
        "journal_dir": str(journal_root),
        "snapshot_path": str(snapshot_path),
        "fills_path": str(fills_path),
        "manifest_path": str(manifest_path),
        "fill_count": fill_count,
        "duplicate_skipped_count": duplicate_skipped_count,
        "candidate_count": manifest_record["candidate_count"],
        "watch_count": manifest_record["watch_count"],
        "quarantine_count": manifest_record["quarantine_count"],
        "include_candidates": manifest_record["include_candidates"],
        "include_quarantine": manifest_record["include_quarantine"],
        "min_reentry_seconds": min_reentry_seconds,
        "paper_only": True,
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
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


def summarize_paper_journal(journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    fills = load_jsonl(journal_root / "paper_fills.jsonl")
    markouts = load_jsonl(journal_root / "markouts.jsonl")
    resolved_audits = load_jsonl(journal_root / "resolved_audits.jsonl")
    manifest = load_jsonl(journal_root / "manifest.jsonl")
    open_fills = [row for row in fills if row.get("status") == "open"]
    return {
        "schema_version": PAPER_JOURNAL_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "manifest_count": len(manifest),
        "paper_fill_count": len(fills),
        "markout_count": len(markouts),
        "resolved_audit_count": len(resolved_audits),
        "open_fill_count": len(open_fills),
        "candidate_fill_count": len([row for row in fills if row.get("signal_bucket") == "candidates"]),
        "watch_fill_count": len([row for row in fills if row.get("signal_bucket") == "watch"]),
        "quarantine_fill_count": len([row for row in fills if row.get("signal_bucket") == "quarantine"]),
    }


def build_markout_record(
    fill: Dict[str, Any],
    *,
    book: Optional[Any],
    recorded_at: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    recorded_at = recorded_at or utc_now_iso()
    entry_price = _safe_float(fill.get("entry_price"))
    age_seconds = _markout_age_seconds(fill.get("recorded_at"), recorded_at)
    current_bid = _safe_float(getattr(book, "best_bid", None)) if book is not None else None
    current_ask = _safe_float(getattr(book, "best_ask", None)) if book is not None else None
    current_spread = _safe_float(getattr(book, "spread", None)) if book is not None else None
    exit_price = current_bid
    markout_cents = (
        round((exit_price - entry_price) * 100.0, 6)
        if exit_price is not None and entry_price is not None
        else None
    )
    markout_pct = (
        round((exit_price / entry_price - 1.0) * 100.0, 6)
        if exit_price is not None and entry_price not in (None, 0)
        else None
    )
    status = "marked"
    if error:
        status = "error"
    elif exit_price is None:
        status = "no_exit_bid"
    markout_id = stable_json_hash(
        {
            "fill_id": fill.get("fill_id"),
            "recorded_at": recorded_at,
            "current_bid": current_bid,
            "current_ask": current_ask,
            "status": status,
        },
        length=24,
    )
    return {
        "schema_version": PAPER_MARKOUT_SCHEMA_VERSION,
        "markout_id": markout_id,
        "fill_id": fill.get("fill_id"),
        "run_id": fill.get("run_id"),
        "recorded_at": recorded_at,
        "status": status,
        "error": error,
        "paper_only": True,
        "signal_bucket": fill.get("signal_bucket"),
        "targeted_shadow": bool(fill.get("targeted_shadow")),
        "targeted_shadow_reasons": fill.get("targeted_shadow_reasons") or [],
        "quarantine_reason": fill.get("quarantine_reason"),
        "quarantine_blocker_scope": fill.get("quarantine_blocker_scope"),
        "quarantine_non_risk_blockers": fill.get("quarantine_non_risk_blockers") or [],
        "quarantine_non_risk_blocker_categories": fill.get("quarantine_non_risk_blocker_categories") or [],
        "would_be_decision_without_risk_rules": fill.get("would_be_decision_without_risk_rules"),
        "would_be_decision_without_quarantine_blockers": fill.get(
            "would_be_decision_without_quarantine_blockers"
        ),
        "city": fill.get("city"),
        "market_family": _market_family_from_record(fill),
        "question": fill.get("question"),
        "token_id": fill.get("token_id"),
        "market_slug": fill.get("market_slug"),
        "side": fill.get("side"),
        "outcome": fill.get("outcome"),
        "bucket_label": fill.get("bucket_label"),
        "bucket_type": fill.get("bucket_type") or _bucket_label_type(fill.get("bucket_label")),
        "strategy_id": fill.get("strategy_id"),
        "execution_style": fill.get("execution_style"),
        "why_now": fill.get("why_now"),
        "strategy_live_eligible": bool(fill.get("strategy_live_eligible", True)),
        "entry_recorded_at": fill.get("recorded_at"),
        "markout_age_seconds": age_seconds,
        "markout_horizon": markout_horizon_label(age_seconds),
        "entry_price": entry_price,
        "entry_bid": _safe_float(fill.get("entry_bid")),
        "entry_ask": _safe_float(fill.get("entry_ask")),
        "entry_spread": _safe_float(fill.get("entry_spread")),
        "entry_liquidity": _safe_float(fill.get("entry_liquidity")),
        "entry_bid_depth_usdc_3c": _safe_float(fill.get("entry_bid_depth_usdc_3c")),
        "entry_ask_depth_usdc_3c": _safe_float(fill.get("entry_ask_depth_usdc_3c")),
        "entry_price_bucket": price_bucket(entry_price),
        "entry_spread_bucket": spread_bucket(fill.get("entry_spread")),
        "entry_edge_bucket": edge_bucket(fill.get("edge_percent")),
        "current_bid": current_bid,
        "current_ask": current_ask,
        "current_spread": current_spread,
        "current_bid_depth_usdc_3c": _safe_float(getattr(book, "bid_depth_usdc_3c", None)) if book is not None else None,
        "current_ask_depth_usdc_3c": _safe_float(getattr(book, "ask_depth_usdc_3c", None)) if book is not None else None,
        "exit_price": exit_price,
        "markout_cents": markout_cents,
        "markout_pct": markout_pct,
        "edge_percent_at_entry": _safe_float(fill.get("edge_percent")),
        "model_probability_at_entry": _safe_float(fill.get("model_probability")),
        "p_lcb_at_entry": _safe_float(fill.get("p_lcb")),
        "q_effective_at_entry": _safe_float(fill.get("q_effective")),
        "cost_at_entry": _safe_float(fill.get("cost")),
        "ev_safe_at_entry": _safe_float(fill.get("ev_safe")),
        "target_date": fill.get("target_date"),
        "end_time": fill.get("end_time"),
        "end_date": fill.get("end_date"),
        "settlement_spec_status": fill.get("settlement_spec_status"),
        "settlement_rule_hash": fill.get("settlement_rule_hash"),
        "settlement_rule_text": fill.get("settlement_rule_text"),
        "settlement_station_code": fill.get("settlement_station_code"),
        "settlement_station_label": fill.get("settlement_station_label"),
        "settlement_source": fill.get("settlement_source"),
        "settlement_timezone": fill.get("settlement_timezone"),
        "settlement_metric": fill.get("settlement_metric"),
        "settlement_unit": fill.get("settlement_unit"),
        "settlement_spec": fill.get("settlement_spec")
        if isinstance(fill.get("settlement_spec"), dict)
        else None,
    }


def markout_open_paper_fills(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    client: Optional[PolymarketReadonlyClient] = None,
    recorded_at: Optional[str] = None,
    max_fills: Optional[int] = None,
    min_markout_interval_seconds: float = 0.0,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    fills = load_jsonl(journal_root / "paper_fills.jsonl")
    recorded_at = recorded_at or utc_now_iso()
    recorded_at_dt = _parse_utc_iso(recorded_at)
    latest_markouts = _latest_records_by_fill_id(load_jsonl(journal_root / "markouts.jsonl"))
    skipped_recent_count = 0
    open_fills = [
        fill
        for fill in fills
        if fill.get("status") == "open" and str(fill.get("token_id") or "").strip()
    ]
    if min_markout_interval_seconds > 0 and recorded_at_dt is not None:
        filtered: List[Dict[str, Any]] = []
        for fill in open_fills:
            latest = latest_markouts.get(str(fill.get("fill_id") or ""))
            latest_dt = _parse_utc_iso((latest or {}).get("recorded_at"))
            if latest_dt is not None:
                age_seconds = (recorded_at_dt - latest_dt).total_seconds()
                if age_seconds >= 0 and age_seconds < float(min_markout_interval_seconds):
                    skipped_recent_count += 1
                    continue
            filtered.append(fill)
        open_fills = filtered
    if max_fills is not None:
        open_fills = open_fills[: max(0, int(max_fills))]
    client = client or PolymarketReadonlyClient()
    records: List[Dict[str, Any]] = []
    for fill in open_fills:
        token_id = str(fill.get("token_id") or "").strip()
        try:
            book = client.get_order_book(token_id)
            records.append(build_markout_record(fill, book=book, recorded_at=recorded_at))
        except PolymarketReadonlyError as exc:
            records.append(build_markout_record(fill, book=None, recorded_at=recorded_at, error=str(exc)))
    markouts_path = journal_root / "markouts.jsonl"
    written = _append_jsonl(markouts_path, records)
    marked = [record for record in records if record.get("status") == "marked"]
    values = [
        _safe_float(record.get("markout_cents"))
        for record in marked
        if _safe_float(record.get("markout_cents")) is not None
    ]
    return {
        "schema_version": PAPER_MARKOUT_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "markouts_path": str(markouts_path),
        "recorded_at": recorded_at,
        "open_fills_seen": len(open_fills),
        "skipped_recent_count": skipped_recent_count,
        "min_markout_interval_seconds": float(min_markout_interval_seconds),
        "markout_records_written": written,
        "marked_count": len(marked),
        "error_count": len([record for record in records if record.get("status") == "error"]),
        "no_exit_bid_count": len([record for record in records if record.get("status") == "no_exit_bid"]),
        "mean_markout_cents": round(sum(values) / len(values), 6) if values else None,
        "records": records,
    }


def _latest_markouts_by_fill_id(markouts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, markout in enumerate(markouts):
        if not isinstance(markout, dict):
            continue
        key = str(markout.get("fill_id") or f"row:{index}")
        latest[key] = markout
    return list(latest.values())


def _with_fill_context(markout: Dict[str, Any], fills_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    fill = fills_by_id.get(str(markout.get("fill_id") or "")) or {}
    merged = dict(markout)
    for key in (
        "city",
        "market_family",
        "question",
        "outcome",
        "bucket_label",
        "side",
        "market_slug",
        "signal_bucket",
        "strategy_id",
        "execution_style",
        "why_now",
        "targeted_shadow",
        "targeted_shadow_reasons",
        "quarantine_reason",
        "quarantine_blocker_scope",
        "would_be_decision_without_risk_rules",
        "would_be_decision_without_quarantine_blockers",
    ):
        if not merged.get(key):
            merged[key] = fill.get(key)
    for key in (
        "target_date",
        "end_time",
        "end_date",
        "settlement_spec_status",
        "settlement_rule_hash",
        "settlement_rule_text",
        "settlement_station_code",
        "settlement_station_label",
        "settlement_source",
        "settlement_timezone",
        "settlement_metric",
        "settlement_unit",
    ):
        if not merged.get(key):
            merged[key] = fill.get(key)
    if not isinstance(merged.get("settlement_spec"), dict) and isinstance(fill.get("settlement_spec"), dict):
        merged["settlement_spec"] = fill.get("settlement_spec")
    for key in ("quarantine_non_risk_blockers", "quarantine_non_risk_blocker_categories"):
        if not merged.get(key):
            merged[key] = fill.get(key) or []
    merged["market_family"] = _market_family_from_record(merged)
    for key in ("entry_bid_depth_usdc_3c", "entry_ask_depth_usdc_3c"):
        if merged.get(key) is None:
            merged[key] = fill.get(key)
    if not merged.get("bucket_type"):
        merged["bucket_type"] = _bucket_label_type(merged.get("bucket_label"))
    if not merged.get("entry_price_bucket"):
        merged["entry_price_bucket"] = price_bucket(merged.get("entry_price") or fill.get("entry_price"))
    if not merged.get("entry_spread_bucket"):
        merged["entry_spread_bucket"] = spread_bucket(merged.get("entry_spread") or fill.get("entry_spread"))
    if not merged.get("entry_edge_bucket"):
        merged["entry_edge_bucket"] = edge_bucket(merged.get("edge_percent_at_entry") or fill.get("edge_percent"))
    if not merged.get("markout_horizon"):
        age_seconds = _markout_age_seconds(
            merged.get("entry_recorded_at") or fill.get("recorded_at"),
            merged.get("recorded_at"),
        )
        merged["markout_age_seconds"] = age_seconds
        merged["markout_horizon"] = markout_horizon_label(age_seconds)
    return merged


def summarize_markout_groups(
    records: Iterable[Dict[str, Any]],
    *,
    group_fields: Iterable[str],
    min_count: int = 1,
) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    fields = [str(field) for field in group_fields]
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "marked":
            continue
        markout = _safe_float(record.get("markout_cents"))
        if markout is None:
            continue
        key = tuple(str(record.get(field) or "unknown") for field in fields)
        groups.setdefault(key, []).append(record)

    rows: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < max(1, int(min_count)):
            continue
        values = [
            float(record["markout_cents"])
            for record in group
            if _safe_float(record.get("markout_cents")) is not None
        ]
        wins = [value for value in values if value > 0.0]
        row = {field: key[index] for index, field in enumerate(fields)}
        row.update(
            {
                "count": len(values),
                "win_count": len(wins),
                "win_rate": round(len(wins) / len(values), 6) if values else None,
                "mean_markout_cents": round(sum(values) / len(values), 6) if values else None,
                "min_markout_cents": round(min(values), 6) if values else None,
                "max_markout_cents": round(max(values), 6) if values else None,
            }
        )
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            float(row.get("mean_markout_cents") or 0.0),
            -int(row.get("count") or 0),
        ),
    )


def _negative_group_flags(
    groups_by_name: Dict[str, List[Dict[str, Any]]],
    *,
    min_count_for_rule: int = 3,
) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    for group_name, rows in groups_by_name.items():
        for row in rows:
            count = int(row.get("count") or 0)
            mean_markout = _safe_float(row.get("mean_markout_cents"))
            win_rate = _safe_float(row.get("win_rate"))
            if mean_markout is None or win_rate is None:
                continue
            if mean_markout >= 0 or win_rate >= 0.55:
                continue
            action = "collect_more_paper"
            if count >= max(1, int(min_count_for_rule)):
                action = "do_not_live_until_positive_markout"
            dimensions = {
                key: value
                for key, value in row.items()
                if key not in {"count", "win_count", "win_rate", "mean_markout_cents", "min_markout_cents", "max_markout_cents"}
            }
            flags.append(
                {
                    "group": group_name,
                    "dimensions": dimensions,
                    "count": count,
                    "win_rate": win_rate,
                    "mean_markout_cents": mean_markout,
                    "action": action,
                }
            )
    return sorted(
        flags,
        key=lambda row: (
            0 if row.get("action") == "do_not_live_until_positive_markout" else 1,
            float(row.get("mean_markout_cents") or 0.0),
            -int(row.get("count") or 0),
        ),
    )


def summarize_markout_strata(
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    *,
    latest_only: bool = True,
    min_count: int = 1,
    quarantine_reasons: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    fills = load_jsonl(journal_root / "paper_fills.jsonl")
    fills_by_id = {str(fill.get("fill_id")): fill for fill in fills if isinstance(fill, dict)}
    markouts = load_jsonl(journal_root / "markouts.jsonl")
    selected = _latest_markouts_by_fill_id(markouts) if latest_only else markouts
    enriched = [_with_fill_context(markout, fills_by_id) for markout in selected]
    if quarantine_reasons is not None:
        allowed_reasons = {
            str(reason or "").strip().lower()
            for reason in quarantine_reasons
            if str(reason or "").strip()
        }
        enriched = [
            record
            for record in enriched
            if str(record.get("quarantine_reason") or "").strip().lower() in allowed_reasons
        ]
    marked = [
        record
        for record in enriched
        if record.get("status") == "marked" and _safe_float(record.get("markout_cents")) is not None
    ]
    values = [float(record["markout_cents"]) for record in marked]
    by_horizon = summarize_markout_groups(
        marked,
        group_fields=("markout_horizon",),
        min_count=min_count,
    )
    by_city = summarize_markout_groups(
        marked,
        group_fields=("city",),
        min_count=min_count,
    )
    by_market_family = summarize_markout_groups(
        marked,
        group_fields=("market_family",),
        min_count=min_count,
    )
    by_bucket_type = summarize_markout_groups(
        marked,
        group_fields=("bucket_type",),
        min_count=min_count,
    )
    by_side = summarize_markout_groups(
        marked,
        group_fields=("side",),
        min_count=min_count,
    )
    by_strategy = summarize_markout_groups(
        marked,
        group_fields=("strategy_id",),
        min_count=min_count,
    )
    by_station = summarize_markout_groups(
        marked,
        group_fields=("settlement_station_code",),
        min_count=min_count,
    )
    by_quarantine_reason = summarize_markout_groups(
        marked,
        group_fields=("quarantine_reason",),
        min_count=min_count,
    )
    by_quarantine_blocker_scope = summarize_markout_groups(
        marked,
        group_fields=("quarantine_blocker_scope",),
        min_count=min_count,
    )
    by_entry_price_bucket = summarize_markout_groups(
        marked,
        group_fields=("entry_price_bucket",),
        min_count=min_count,
    )
    by_entry_spread_bucket = summarize_markout_groups(
        marked,
        group_fields=("entry_spread_bucket",),
        min_count=min_count,
    )
    by_horizon_and_spread = summarize_markout_groups(
        marked,
        group_fields=("markout_horizon", "entry_spread_bucket"),
        min_count=min_count,
    )
    by_strategy_and_horizon = summarize_markout_groups(
        marked,
        group_fields=("strategy_id", "markout_horizon"),
        min_count=min_count,
    )
    by_station_and_horizon = summarize_markout_groups(
        marked,
        group_fields=("settlement_station_code", "markout_horizon"),
        min_count=min_count,
    )
    groups_by_name = {
        "by_horizon": by_horizon,
        "by_city": by_city,
        "by_market_family": by_market_family,
        "by_bucket_type": by_bucket_type,
        "by_side": by_side,
        "by_strategy": by_strategy,
        "by_station": by_station,
        "by_quarantine_reason": by_quarantine_reason,
        "by_quarantine_blocker_scope": by_quarantine_blocker_scope,
        "by_entry_price_bucket": by_entry_price_bucket,
        "by_entry_spread_bucket": by_entry_spread_bucket,
        "by_horizon_and_spread": by_horizon_and_spread,
        "by_strategy_and_horizon": by_strategy_and_horizon,
        "by_station_and_horizon": by_station_and_horizon,
    }
    return {
        "schema_version": PAPER_MARKOUT_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "latest_only": bool(latest_only),
        "quarantine_reasons_filter": (
            sorted(
                {
                    str(reason or "").strip()
                    for reason in quarantine_reasons
                    if str(reason or "").strip()
                }
            )
            if quarantine_reasons is not None
            else None
        ),
        "markout_observation_count": len(markouts),
        "selected_markout_count": len(selected),
        "marked_count": len(marked),
        "mean_markout_cents": round(sum(values) / len(values), 6) if values else None,
        "win_rate": round(len([value for value in values if value > 0.0]) / len(values), 6) if values else None,
        "by_horizon": by_horizon,
        "by_city": by_city,
        "by_market_family": by_market_family,
        "by_bucket_type": by_bucket_type,
        "by_side": by_side,
        "by_strategy": by_strategy,
        "by_station": by_station,
        "by_quarantine_reason": by_quarantine_reason,
        "by_quarantine_blocker_scope": by_quarantine_blocker_scope,
        "by_entry_price_bucket": by_entry_price_bucket,
        "by_entry_spread_bucket": by_entry_spread_bucket,
        "by_horizon_and_spread": by_horizon_and_spread,
        "by_strategy_and_horizon": by_strategy_and_horizon,
        "by_station_and_horizon": by_station_and_horizon,
        "do_not_live_rules": _negative_group_flags(groups_by_name),
    }
