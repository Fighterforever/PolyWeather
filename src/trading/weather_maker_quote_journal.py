from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.polymarket_readonly import PolymarketReadonlyClient, PolymarketReadonlyError
from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _append_jsonl,
    _bucket_label_type,
    _market_family_from_record,
    _markout_age_seconds,
    _parse_utc_iso,
    _safe_float,
    load_jsonl,
    markout_horizon_label,
    price_bucket,
    spread_bucket,
    stable_json_hash,
    utc_now_iso,
)
from src.trading.weather_signal_risk_filter import fine_time_to_expiry_bucket, time_to_expiry_bucket


MAKER_QUOTE_SCHEMA_VERSION = "polyweather_weather_maker_quote.v1"
MAKER_QUOTE_MARKOUT_SCHEMA_VERSION = "polyweather_weather_maker_quote_markout.v1"


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _quote_dedupe_key(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(record.get("fill_id") or "").strip(),
        str(record.get("token_id") or "").strip(),
        str(record.get("quote_price") or "").strip(),
        str(record.get("quote_strategy") or "maker_bid").strip(),
    )


def _normalized_quote_offsets_cents(quote_offset_cents: Optional[Iterable[float]]) -> List[float]:
    raw_offsets = quote_offset_cents if quote_offset_cents is not None else (0.0,)
    offsets: List[float] = []
    seen = set()
    for value in raw_offsets:
        offset = _safe_float(value)
        if offset is None or offset < 0.0:
            continue
        normalized = round(offset, 6)
        key = f"{normalized:.6f}"
        if key in seen:
            continue
        seen.add(key)
        offsets.append(normalized)
    return offsets or [0.0]


def _quote_strategy_for_offset(offset_cents: float) -> str:
    if abs(float(offset_cents)) < 1e-9:
        return "maker_bid"
    text = ("%g" % float(offset_cents)).replace(".", "_")
    return f"maker_bid_minus_{text}c"


def _filter_duplicate_open_quotes(
    records: Iterable[Dict[str, Any]],
    *,
    existing_quotes: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    existing_keys = {
        _quote_dedupe_key(row)
        for row in existing_quotes
        if isinstance(row, dict) and row.get("status") == "open"
    }
    filtered: List[Dict[str, Any]] = []
    duplicate_count = 0
    seen_new = set()
    for record in records:
        key = _quote_dedupe_key(record)
        if key in existing_keys or key in seen_new:
            duplicate_count += 1
            continue
        filtered.append(record)
        seen_new.add(key)
    return filtered, duplicate_count


def build_maker_quote_records(
    fills: Iterable[Dict[str, Any]],
    *,
    quote_size: float = 1.0,
    quote_offset_cents: Optional[Iterable[float]] = None,
    recorded_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    recorded_at = recorded_at or utc_now_iso()
    size = max(0.0, float(quote_size))
    offsets = _normalized_quote_offsets_cents(quote_offset_cents)
    records: List[Dict[str, Any]] = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        token_id = str(fill.get("token_id") or "").strip()
        reference_price = _safe_float(fill.get("entry_bid"))
        if not token_id or reference_price is None or reference_price <= 0:
            continue
        for offset_cents in offsets:
            quote_price = round(reference_price - (float(offset_cents) / 100.0), 6)
            if quote_price <= 0.0:
                continue
            quote_strategy = _quote_strategy_for_offset(offset_cents)
            quote_identity = {
                "fill_id": fill.get("fill_id"),
                "token_id": token_id,
                "quote_price": quote_price,
                "quote_strategy": quote_strategy,
            }
            quote_id = stable_json_hash(quote_identity, length=24)
            records.append(
                {
                    "schema_version": MAKER_QUOTE_SCHEMA_VERSION,
                    "quote_id": quote_id,
                    "fill_id": fill.get("fill_id"),
                    "run_id": fill.get("run_id"),
                    "recorded_at": recorded_at,
                    "paper_only": True,
                    "status": "open",
                    "counts_for_live_gate": False,
                    "quote_strategy": quote_strategy,
                    "quote_side": "buy",
                    "quote_reference_price": reference_price,
                    "quote_offset_cents": float(offset_cents),
                    "quote_price": quote_price,
                    "quote_size": size,
                    "quote_notional_usdc": round(quote_price * size, 6),
                    "city": fill.get("city"),
                    "market_family": _market_family_from_record(fill),
                    "question": fill.get("question"),
                    "market_slug": fill.get("market_slug"),
                    "token_id": token_id,
                    "side": fill.get("side"),
                    "outcome": fill.get("outcome"),
                    "bucket_label": fill.get("bucket_label"),
                    "signal_bucket": fill.get("signal_bucket"),
                    "decision": fill.get("decision"),
                    "entry_ask": _safe_float(fill.get("entry_ask") or fill.get("entry_price")),
                    "entry_bid": _safe_float(fill.get("entry_bid")),
                    "entry_spread": _safe_float(fill.get("entry_spread")),
                    "entry_liquidity": _safe_float(fill.get("entry_liquidity")),
                    "edge_percent": _safe_float(fill.get("edge_percent")),
                    "model_probability": _safe_float(fill.get("model_probability")),
                    "market_probability": _safe_float(fill.get("market_probability")),
                    "end_date": fill.get("end_date"),
                    "time_to_expiry_bucket": time_to_expiry_bucket(
                        fill.get("end_date"),
                        now=recorded_at,
                    ),
                    "fine_time_to_expiry_bucket": fine_time_to_expiry_bucket(
                        fill.get("end_date"),
                        now=recorded_at,
                    ),
                    "source_fill_recorded_at": fill.get("recorded_at"),
                    "risk_rule_hits": fill.get("risk_rule_hits") or [],
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
                }
            )
    return records


def write_maker_quote_journal_from_fills(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    quote_size: float = 1.0,
    quote_offset_cents: Optional[Iterable[float]] = None,
    max_quotes: Optional[int] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    recorded_at = recorded_at or utc_now_iso()
    fills = [
        fill
        for fill in load_jsonl(journal_root / "paper_fills.jsonl")
        if fill.get("status") == "open" and str(fill.get("token_id") or "").strip()
    ]
    records = build_maker_quote_records(
        fills,
        quote_size=quote_size,
        quote_offset_cents=quote_offset_cents,
        recorded_at=recorded_at,
    )
    if max_quotes is not None:
        records = records[: max(0, int(max_quotes))]
    quotes_path = journal_root / "maker_quotes.jsonl"
    records, duplicate_skipped_count = _filter_duplicate_open_quotes(
        records,
        existing_quotes=load_jsonl(quotes_path),
    )
    written = _append_jsonl(quotes_path, records)
    return {
        "schema_version": MAKER_QUOTE_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "quotes_path": str(quotes_path),
        "recorded_at": recorded_at,
        "paper_fills_seen": len(fills),
        "quote_records_written": written,
        "duplicate_skipped_count": duplicate_skipped_count,
        "quote_offset_cents": _normalized_quote_offsets_cents(quote_offset_cents),
        "paper_only": True,
        "counts_for_live_gate": False,
    }


def build_maker_quote_markout_record(
    quote: Dict[str, Any],
    *,
    book: Optional[Any],
    recorded_at: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    recorded_at = recorded_at or utc_now_iso()
    quote_price = _safe_float(quote.get("quote_price"))
    entry_ask = _safe_float(quote.get("entry_ask"))
    current_bid = _safe_float(getattr(book, "best_bid", None)) if book is not None else None
    current_ask = _safe_float(getattr(book, "best_ask", None)) if book is not None else None
    current_spread = _safe_float(getattr(book, "spread", None)) if book is not None else None
    age_seconds = _markout_age_seconds(quote.get("recorded_at"), recorded_at)

    inferred_fill = bool(
        error is None
        and quote_price is not None
        and current_ask is not None
        and current_ask <= quote_price
    )
    status = "inferred_filled" if inferred_fill else "resting_unfilled"
    if error:
        status = "error"
    elif quote_price is None:
        status = "invalid_quote"
    elif current_bid is None and current_ask is None:
        status = "no_book"

    maker_markout_cents = (
        round((current_bid - quote_price) * 100.0, 6)
        if inferred_fill and current_bid is not None and quote_price is not None
        else None
    )
    missed_taker_markout_cents = (
        round((current_bid - entry_ask) * 100.0, 6)
        if not inferred_fill and current_bid is not None and entry_ask is not None
        else None
    )
    markout_id = stable_json_hash(
        {
            "quote_id": quote.get("quote_id"),
            "recorded_at": recorded_at,
            "current_bid": current_bid,
            "current_ask": current_ask,
            "status": status,
        },
        length=24,
    )
    return {
        "schema_version": MAKER_QUOTE_MARKOUT_SCHEMA_VERSION,
        "markout_id": markout_id,
        "quote_id": quote.get("quote_id"),
        "fill_id": quote.get("fill_id"),
        "run_id": quote.get("run_id"),
        "recorded_at": recorded_at,
        "status": status,
        "error": error,
        "paper_only": True,
        "counts_for_live_gate": False,
        "fill_inference": "current_ask_at_or_below_quote" if inferred_fill else "not_crossed_by_current_book",
        "quote_recorded_at": quote.get("recorded_at"),
        "quote_age_seconds": age_seconds,
        "quote_horizon": markout_horizon_label(age_seconds),
        "quote_strategy": quote.get("quote_strategy") or "maker_bid",
        "quote_side": quote.get("quote_side") or "buy",
        "quote_reference_price": _safe_float(quote.get("quote_reference_price")),
        "quote_offset_cents": _safe_float(quote.get("quote_offset_cents")),
        "quote_price": quote_price,
        "maker_quote_price_bucket": price_bucket(quote_price),
        "quote_size": _safe_float(quote.get("quote_size")),
        "quote_notional_usdc": _safe_float(quote.get("quote_notional_usdc")),
        "entry_ask": entry_ask,
        "entry_bid": _safe_float(quote.get("entry_bid")),
        "entry_spread": _safe_float(quote.get("entry_spread")),
        "entry_spread_bucket": spread_bucket(quote.get("entry_spread")),
        "time_to_expiry_bucket": quote.get("time_to_expiry_bucket")
        or time_to_expiry_bucket(quote.get("end_date"), now=quote.get("recorded_at")),
        "fine_time_to_expiry_bucket": quote.get("fine_time_to_expiry_bucket")
        or fine_time_to_expiry_bucket(quote.get("end_date"), now=quote.get("recorded_at")),
        "current_bid": current_bid,
        "current_ask": current_ask,
        "current_spread": current_spread,
        "current_bid_depth_usdc_3c": _safe_float(getattr(book, "bid_depth_usdc_3c", None)) if book is not None else None,
        "current_ask_depth_usdc_3c": _safe_float(getattr(book, "ask_depth_usdc_3c", None)) if book is not None else None,
        "maker_markout_cents": maker_markout_cents,
        "missed_taker_markout_cents": missed_taker_markout_cents,
        "city": quote.get("city"),
        "market_family": _market_family_from_record(quote),
        "question": quote.get("question"),
        "market_slug": quote.get("market_slug"),
        "token_id": quote.get("token_id"),
        "side": quote.get("side"),
        "outcome": quote.get("outcome"),
        "bucket_label": quote.get("bucket_label"),
        "bucket_type": _bucket_label_type(quote.get("bucket_label")),
        "signal_bucket": quote.get("signal_bucket"),
        "decision": quote.get("decision"),
        "quarantine_reason": quote.get("quarantine_reason"),
        "quarantine_blocker_scope": quote.get("quarantine_blocker_scope"),
        "quarantine_non_risk_blockers": quote.get("quarantine_non_risk_blockers") or [],
        "quarantine_non_risk_blocker_categories": quote.get("quarantine_non_risk_blocker_categories") or [],
        "would_be_decision_without_risk_rules": quote.get("would_be_decision_without_risk_rules"),
        "would_be_decision_without_quarantine_blockers": quote.get(
            "would_be_decision_without_quarantine_blockers"
        ),
        "edge_percent": _safe_float(quote.get("edge_percent")),
        "model_probability": _safe_float(quote.get("model_probability")),
    }


def markout_open_maker_quotes(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    client: Optional[PolymarketReadonlyClient] = None,
    recorded_at: Optional[str] = None,
    max_quotes: Optional[int] = None,
    min_markout_interval_seconds: float = 0.0,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    markouts_path = journal_root / "maker_quote_markouts.jsonl"
    fills_by_id = {
        str(fill.get("fill_id")): fill
        for fill in load_jsonl(journal_root / "paper_fills.jsonl")
        if isinstance(fill, dict)
    }
    quotes = [
        quote
        for quote in load_jsonl(journal_root / "maker_quotes.jsonl")
        if quote.get("status") == "open" and str(quote.get("token_id") or "").strip()
    ]
    recorded_at = recorded_at or utc_now_iso()
    recorded_at_dt = _parse_utc_iso(recorded_at)
    latest_markouts = _latest_records_by_quote_id(load_jsonl(markouts_path))
    skipped_recent_count = 0
    if min_markout_interval_seconds > 0 and recorded_at_dt is not None:
        filtered: List[Dict[str, Any]] = []
        for quote in quotes:
            latest = latest_markouts.get(str(quote.get("quote_id") or ""))
            latest_dt = _parse_utc_iso((latest or {}).get("recorded_at"))
            if latest_dt is not None:
                age_seconds = (recorded_at_dt - latest_dt).total_seconds()
                if age_seconds >= 0 and age_seconds < float(min_markout_interval_seconds):
                    skipped_recent_count += 1
                    continue
            filtered.append(quote)
        quotes = filtered
    if max_quotes is not None:
        quotes = quotes[: max(0, int(max_quotes))]
    client = client or PolymarketReadonlyClient()
    records: List[Dict[str, Any]] = []
    for quote in quotes:
        token_id = str(quote.get("token_id") or "").strip()
        fill_context = fills_by_id.get(str(quote.get("fill_id") or ""), {})
        contextual_quote = {**fill_context, **quote}
        try:
            book = client.get_order_book(token_id)
            records.append(build_maker_quote_markout_record(contextual_quote, book=book, recorded_at=recorded_at))
        except PolymarketReadonlyError as exc:
            records.append(build_maker_quote_markout_record(contextual_quote, book=None, recorded_at=recorded_at, error=str(exc)))
    written = _append_jsonl(markouts_path, records)
    summary = summarize_maker_quote_markouts(records)
    return {
        "schema_version": MAKER_QUOTE_MARKOUT_SCHEMA_VERSION,
        "journal_dir": str(journal_root),
        "markouts_path": str(markouts_path),
        "recorded_at": recorded_at,
        "open_quotes_seen": len(quotes),
        "skipped_recent_count": skipped_recent_count,
        "min_markout_interval_seconds": float(min_markout_interval_seconds),
        "markout_records_written": written,
        **summary,
    }


def _latest_by_quote_id(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = str(record.get("quote_id") or f"row:{index}")
        latest[key] = record
    return list(latest.values())


def _latest_records_by_quote_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = str(record.get("quote_id") or f"row:{index}")
        latest[key] = record
    return latest


def _with_maker_quote_context(
    markout: Dict[str, Any],
    *,
    quotes_by_id: Dict[str, Dict[str, Any]],
    fills_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    quote = quotes_by_id.get(str(markout.get("quote_id") or "")) or {}
    fill = fills_by_id.get(str(markout.get("fill_id") or quote.get("fill_id") or "")) or {}
    merged = {**fill, **quote, **markout}
    for key in ("city", "question", "market_slug", "bucket_label", "side", "outcome"):
        if not merged.get(key):
            merged[key] = quote.get(key) or fill.get(key)
    merged["market_family"] = _market_family_from_record(merged)
    if not merged.get("bucket_type"):
        merged["bucket_type"] = _bucket_label_type(merged.get("bucket_label"))
    if not merged.get("fine_time_to_expiry_bucket"):
        merged["fine_time_to_expiry_bucket"] = fine_time_to_expiry_bucket(
            merged.get("end_date"),
            now=merged.get("quote_recorded_at") or merged.get("recorded_at"),
        )
    return merged


def summarize_maker_quote_markouts(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [record for record in records if isinstance(record, dict)]
    inferred = [record for record in rows if record.get("status") == "inferred_filled"]
    unfilled = [record for record in rows if record.get("status") == "resting_unfilled"]
    maker_values = [
        float(record["maker_markout_cents"])
        for record in inferred
        if _safe_float(record.get("maker_markout_cents")) is not None
    ]
    missed_values = [
        float(record["missed_taker_markout_cents"])
        for record in unfilled
        if _safe_float(record.get("missed_taker_markout_cents")) is not None
    ]
    return {
        "quote_markout_count": len(rows),
        "inferred_fill_count": len(inferred),
        "resting_unfilled_count": len(unfilled),
        "error_count": len([record for record in rows if record.get("status") == "error"]),
        "no_book_count": len([record for record in rows if record.get("status") == "no_book"]),
        "fill_inference_rate": _ratio(len(inferred), len(rows)),
        "maker_markout_win_count": len([value for value in maker_values if value > 0.0]),
        "maker_markout_win_rate": _ratio(len([value for value in maker_values if value > 0.0]), len(maker_values)),
        "mean_maker_markout_cents": _mean(maker_values),
        "mean_missed_taker_markout_cents": _mean(missed_values),
    }


def summarize_maker_quote_journal(
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    *,
    latest_only: bool = True,
    quarantine_reasons: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    quotes = load_jsonl(journal_root / "maker_quotes.jsonl")
    quotes_by_id = {str(quote.get("quote_id")): quote for quote in quotes if isinstance(quote, dict)}
    fills_by_id = {
        str(fill.get("fill_id")): fill
        for fill in load_jsonl(journal_root / "paper_fills.jsonl")
        if isinstance(fill, dict)
    }
    markouts = load_jsonl(journal_root / "maker_quote_markouts.jsonl")
    selected_raw = _latest_by_quote_id(markouts) if latest_only else markouts
    selected = [
        _with_maker_quote_context(row, quotes_by_id=quotes_by_id, fills_by_id=fills_by_id)
        for row in selected_raw
        if isinstance(row, dict)
    ]
    if quarantine_reasons is not None:
        allowed_reasons = {
            str(reason or "").strip().lower()
            for reason in quarantine_reasons
            if str(reason or "").strip()
        }
        selected = [
            row
            for row in selected
            if str(row.get("quarantine_reason") or "").strip().lower() in allowed_reasons
        ]
    return {
        "schema_version": MAKER_QUOTE_MARKOUT_SCHEMA_VERSION,
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
        "quote_count": len(quotes),
        "markout_observation_count": len(markouts),
        **summarize_maker_quote_markouts(selected),
    }


def summarize_maker_quote_groups(
    records: Iterable[Dict[str, Any]],
    *,
    group_fields: Iterable[str],
    min_count: int = 1,
) -> List[Dict[str, Any]]:
    fields = [str(field) for field in group_fields]
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("status") not in {"inferred_filled", "resting_unfilled"}:
            continue
        key = tuple(str(record.get(field) or "unknown").strip().lower() for field in fields)
        groups.setdefault(key, []).append(record)

    rows: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < max(1, int(min_count)):
            continue
        inferred = [record for record in group if record.get("status") == "inferred_filled"]
        maker_values = [
            float(record["maker_markout_cents"])
            for record in inferred
            if _safe_float(record.get("maker_markout_cents")) is not None
        ]
        missed_values = [
            float(record["missed_taker_markout_cents"])
            for record in group
            if record.get("status") == "resting_unfilled"
            and _safe_float(record.get("missed_taker_markout_cents")) is not None
        ]
        row = {field: key[index] for index, field in enumerate(fields)}
        row.update(
            {
                "count": len(group),
                "inferred_fill_count": len(inferred),
                "fill_inference_rate": _ratio(len(inferred), len(group)),
                "maker_markout_win_count": len([value for value in maker_values if value > 0.0]),
                "maker_markout_win_rate": _ratio(
                    len([value for value in maker_values if value > 0.0]),
                    len(maker_values),
                ),
                "mean_maker_markout_cents": _mean(maker_values),
                "mean_missed_taker_markout_cents": _mean(missed_values),
            }
        )
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            float(row.get("mean_maker_markout_cents") or 0.0),
            -int(row.get("inferred_fill_count") or 0),
            -int(row.get("count") or 0),
        ),
    )


def _maker_quote_negative_group_flags(
    groups_by_name: Dict[str, List[Dict[str, Any]]],
    *,
    min_count_for_rule: int = 3,
    min_inferred_fills_for_rule: int = 1,
) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    for group_name, rows in groups_by_name.items():
        for row in rows:
            count = int(row.get("count") or 0)
            inferred_count = int(row.get("inferred_fill_count") or 0)
            fill_rate = _safe_float(row.get("fill_inference_rate"))
            maker_mean = _safe_float(row.get("mean_maker_markout_cents"))
            maker_win_rate = _safe_float(row.get("maker_markout_win_rate"))
            if count < max(1, int(min_count_for_rule)):
                continue
            action_reason = None
            if inferred_count >= max(1, int(min_inferred_fills_for_rule)):
                if maker_mean is None or maker_mean <= 0.0 or (maker_win_rate is not None and maker_win_rate < 0.55):
                    action_reason = "maker_adverse_selection"
            elif fill_rate == 0.0:
                action_reason = "maker_no_fill_evidence"
            if not action_reason:
                continue
            dimensions = {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "count",
                    "inferred_fill_count",
                    "fill_inference_rate",
                    "maker_markout_win_count",
                    "maker_markout_win_rate",
                    "mean_maker_markout_cents",
                    "mean_missed_taker_markout_cents",
                }
            }
            flags.append(
                {
                    "action": "do_not_live_until_positive_markout",
                    "source": "maker_quote_strata",
                    "group": f"maker_quote_{group_name}",
                    "dimensions": dimensions,
                    "count": count,
                    "inferred_fill_count": inferred_count,
                    "fill_inference_rate": fill_rate,
                    "maker_markout_win_rate": maker_win_rate,
                    "mean_markout_cents": maker_mean,
                    "mean_maker_markout_cents": maker_mean,
                    "mean_missed_taker_markout_cents": _safe_float(row.get("mean_missed_taker_markout_cents")),
                    "reason": action_reason,
                }
            )
    return sorted(
        flags,
        key=lambda row: (
            0 if row.get("reason") == "maker_adverse_selection" else 1,
            float(row.get("mean_maker_markout_cents") or 0.0),
            -int(row.get("count") or 0),
        ),
    )


def summarize_maker_quote_strata(
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    *,
    latest_only: bool = True,
    min_count: int = 1,
    quarantine_reasons: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    journal_root = Path(journal_dir)
    quotes_by_id = {
        str(quote.get("quote_id")): quote
        for quote in load_jsonl(journal_root / "maker_quotes.jsonl")
        if isinstance(quote, dict)
    }
    fills_by_id = {
        str(fill.get("fill_id")): fill
        for fill in load_jsonl(journal_root / "paper_fills.jsonl")
        if isinstance(fill, dict)
    }
    markouts = load_jsonl(journal_root / "maker_quote_markouts.jsonl")
    selected_raw = _latest_by_quote_id(markouts) if latest_only else markouts
    selected = [
        _with_maker_quote_context(row, quotes_by_id=quotes_by_id, fills_by_id=fills_by_id)
        for row in selected_raw
        if isinstance(row, dict)
    ]
    if quarantine_reasons is not None:
        allowed_reasons = {
            str(reason or "").strip().lower()
            for reason in quarantine_reasons
            if str(reason or "").strip()
        }
        selected = [
            row
            for row in selected
            if str(row.get("quarantine_reason") or "").strip().lower() in allowed_reasons
        ]
    by_city = summarize_maker_quote_groups(selected, group_fields=("city",), min_count=min_count)
    by_market_family = summarize_maker_quote_groups(
        selected,
        group_fields=("market_family",),
        min_count=min_count,
    )
    by_bucket_type = summarize_maker_quote_groups(selected, group_fields=("bucket_type",), min_count=min_count)
    by_quarantine_reason = summarize_maker_quote_groups(
        selected,
        group_fields=("quarantine_reason",),
        min_count=min_count,
    )
    by_quarantine_blocker_scope = summarize_maker_quote_groups(
        selected,
        group_fields=("quarantine_blocker_scope",),
        min_count=min_count,
    )
    by_quote_price_bucket = summarize_maker_quote_groups(
        selected,
        group_fields=("maker_quote_price_bucket",),
        min_count=min_count,
    )
    by_quote_strategy = summarize_maker_quote_groups(
        selected,
        group_fields=("quote_strategy",),
        min_count=min_count,
    )
    by_entry_spread_bucket = summarize_maker_quote_groups(
        selected,
        group_fields=("entry_spread_bucket",),
        min_count=min_count,
    )
    by_time_to_expiry = summarize_maker_quote_groups(
        selected,
        group_fields=("time_to_expiry_bucket",),
        min_count=min_count,
    )
    by_fine_time_to_expiry = summarize_maker_quote_groups(
        selected,
        group_fields=("fine_time_to_expiry_bucket",),
        min_count=min_count,
    )
    by_city_and_bucket_type = summarize_maker_quote_groups(
        selected,
        group_fields=("city", "bucket_type"),
        min_count=min_count,
    )
    by_time_to_expiry_and_spread = summarize_maker_quote_groups(
        selected,
        group_fields=("time_to_expiry_bucket", "entry_spread_bucket"),
        min_count=min_count,
    )
    by_fine_time_to_expiry_and_spread = summarize_maker_quote_groups(
        selected,
        group_fields=("fine_time_to_expiry_bucket", "entry_spread_bucket"),
        min_count=min_count,
    )
    by_strategy_and_spread = summarize_maker_quote_groups(
        selected,
        group_fields=("quote_strategy", "entry_spread_bucket"),
        min_count=min_count,
    )
    by_strategy_and_time_to_expiry = summarize_maker_quote_groups(
        selected,
        group_fields=("quote_strategy", "time_to_expiry_bucket"),
        min_count=min_count,
    )
    by_strategy_and_fine_time_to_expiry_and_spread = summarize_maker_quote_groups(
        selected,
        group_fields=("quote_strategy", "fine_time_to_expiry_bucket", "entry_spread_bucket"),
        min_count=min_count,
    )
    groups_by_name = {
        "by_city": by_city,
        "by_market_family": by_market_family,
        "by_bucket_type": by_bucket_type,
        "by_quarantine_reason": by_quarantine_reason,
        "by_quarantine_blocker_scope": by_quarantine_blocker_scope,
        "by_quote_price_bucket": by_quote_price_bucket,
        "by_quote_strategy": by_quote_strategy,
        "by_entry_spread_bucket": by_entry_spread_bucket,
        "by_time_to_expiry": by_time_to_expiry,
        "by_fine_time_to_expiry": by_fine_time_to_expiry,
        "by_city_and_bucket_type": by_city_and_bucket_type,
        "by_time_to_expiry_and_spread": by_time_to_expiry_and_spread,
        "by_fine_time_to_expiry_and_spread": by_fine_time_to_expiry_and_spread,
        "by_strategy_and_spread": by_strategy_and_spread,
        "by_strategy_and_time_to_expiry": by_strategy_and_time_to_expiry,
        "by_strategy_and_fine_time_to_expiry_and_spread": by_strategy_and_fine_time_to_expiry_and_spread,
    }
    return {
        "schema_version": MAKER_QUOTE_MARKOUT_SCHEMA_VERSION,
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
        **summarize_maker_quote_markouts(selected),
        "by_city": by_city,
        "by_market_family": by_market_family,
        "by_bucket_type": by_bucket_type,
        "by_quarantine_reason": by_quarantine_reason,
        "by_quarantine_blocker_scope": by_quarantine_blocker_scope,
        "by_quote_price_bucket": by_quote_price_bucket,
        "by_quote_strategy": by_quote_strategy,
        "by_entry_spread_bucket": by_entry_spread_bucket,
        "by_time_to_expiry": by_time_to_expiry,
        "by_fine_time_to_expiry": by_fine_time_to_expiry,
        "by_city_and_bucket_type": by_city_and_bucket_type,
        "by_time_to_expiry_and_spread": by_time_to_expiry_and_spread,
        "by_fine_time_to_expiry_and_spread": by_fine_time_to_expiry_and_spread,
        "by_strategy_and_spread": by_strategy_and_spread,
        "by_strategy_and_time_to_expiry": by_strategy_and_time_to_expiry,
        "by_strategy_and_fine_time_to_expiry_and_spread": by_strategy_and_fine_time_to_expiry_and_spread,
        "do_not_live_rules": _maker_quote_negative_group_flags(groups_by_name),
    }


def dump_maker_quote_summary(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
