from __future__ import annotations

import math
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements
from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _safe_float,
    load_jsonl,
    utc_now_iso,
)
from src.trading.weather_replay import replay_taker_candidates
from src.trading.weather_strict_gate_queue import (
    DEFAULT_STRICT_GATE_QUEUE_DIR,
    strict_gate_queue_records_as_replay_candidates,
)


STRICT_GATE_REPLAY_SCHEMA_VERSION = "polyweather_weather_strict_gate_replay.v1"


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 8)


def _count_by(records: Iterable[Dict[str, Any]], field: str, *, key_name: Optional[str] = None) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return [
        {key_name or field: value, "count": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _resolved_outcomes_from_audits(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outcomes: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "resolved":
            continue
        token_id = str(record.get("token_id") or "").strip()
        payout = _safe_float(record.get("payout"))
        if token_id and payout is not None:
            outcomes.append(
                {
                    "token_id": token_id,
                    "payout": float(payout),
                    "fill_id": record.get("fill_id"),
                    "market_slug": record.get("market_slug"),
                    "resolution_source": record.get("resolution_source"),
                    "resolution_record_source": "resolved_audit",
                }
            )
    return outcomes


def _resolved_outcomes_from_backfill(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outcomes: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "resolved":
            continue
        settled = record.get("settled_probability_by_outcome")
        if not isinstance(settled, dict):
            settled = {}
        token_id_by_outcome = record.get("token_id_by_outcome")
        if not isinstance(token_id_by_outcome, dict):
            token_id_by_outcome = {}
        for outcome in record.get("outcomes") or []:
            outcome_text = str(outcome or "").strip()
            if not outcome_text:
                continue
            token_id = str(token_id_by_outcome.get(outcome_text) or "").strip()
            if not token_id and outcome_text == str(record.get("winning_outcome") or "").strip():
                token_id = str(record.get("winning_token_id") or "").strip()
            payout = _safe_float(settled.get(outcome_text))
            if token_id and payout is not None:
                outcomes.append(
                    {
                        "token_id": token_id,
                        "payout": float(payout),
                        "market_id": record.get("market_id"),
                        "market_slug": record.get("market_slug"),
                        "outcome": outcome_text,
                        "winning_token_id": record.get("winning_token_id"),
                        "resolution_source": record.get("resolution_source") or "closed_backfill",
                        "resolution_record_source": "closed_backfill",
                        "official_final_value": record.get("official_final_value"),
                        "market_inferred_final_value": record.get("market_inferred_final_value"),
                        "market_inferred_final_value_source": record.get("market_inferred_final_value_source"),
                        "resolution_rule_hash": record.get("rule_hash"),
                    }
                )
    return outcomes


def _dedupe_resolved_outcomes(
    outcomes: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_token: Dict[str, Dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        token_id = str(outcome.get("token_id") or "").strip()
        payout = _safe_float(outcome.get("payout"))
        if not token_id or payout is None:
            continue
        by_token[token_id] = outcome
    return list(by_token.values())


def resolved_outcomes_from_audits_and_backfill(
    *,
    audit_records: Iterable[Dict[str, Any]],
    backfill_records: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return token-level payouts, preferring resolved audits over backfill."""

    backfill = _resolved_outcomes_from_backfill(backfill_records)
    audits = _resolved_outcomes_from_audits(audit_records)
    return _dedupe_resolved_outcomes([*backfill, *audits])


def _queue_summary(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_queue: Dict[str, Dict[str, Any]] = {}
    for record in records:
        queue_name = str(record.get("queue_name") or "unknown")
        row = by_queue.setdefault(
            queue_name,
            {
                "queue_name": queue_name,
                "record_count": 0,
                "with_token_count": 0,
                "mean_ev_safe": None,
                "ev_values": [],
                "top_reasons": {},
            },
        )
        row["record_count"] += 1
        if str(record.get("token_id") or "").strip():
            row["with_token_count"] += 1
        ev_safe = _safe_float(record.get("ev_safe"))
        if ev_safe is not None:
            row["ev_values"].append(float(ev_safe))
        for reason in record.get("queue_reasons") or []:
            text = str(reason or "").strip()
            if text:
                row["top_reasons"][text] = int(row["top_reasons"].get(text) or 0) + 1
    rows: List[Dict[str, Any]] = []
    for row in by_queue.values():
        ev_values = row.pop("ev_values")
        reason_counts = row.pop("top_reasons")
        row["mean_ev_safe"] = _mean(ev_values)
        row["top_reasons"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
        ]
        rows.append(row)
    return sorted(rows, key=lambda row: (-int(row.get("record_count") or 0), str(row.get("queue_name") or "")))


def _execution_summary(replay: Dict[str, Any]) -> Dict[str, Any]:
    fills = [row for row in replay.get("fills") or [] if isinstance(row, dict)]
    entry_minus_q_cents: List[float] = []
    ev_minus_execution_cost_cents: List[float] = []
    for fill in fills:
        entry_price = _safe_float(fill.get("entry_price"))
        q_effective = _safe_float(fill.get("q_effective"))
        ev_safe = _safe_float(fill.get("ev_safe"))
        if entry_price is not None and q_effective is not None:
            entry_minus_q_cents.append((float(entry_price) - float(q_effective)) * 100.0)
        if ev_safe is not None and entry_price is not None and q_effective is not None:
            ev_minus_execution_cost_cents.append(
                (float(ev_safe) - max(0.0, float(entry_price) - float(q_effective))) * 100.0
            )
    return {
        "fill_count": len(fills),
        "fully_filled_count": len([row for row in fills if row.get("fully_filled") is True]),
        "missed_fill_count": len([row for row in fills if row.get("missed_fill") is True]),
        "mean_entry_minus_q_effective_cents": _mean(entry_minus_q_cents),
        "mean_ev_after_depth_cost_cents": _mean(ev_minus_execution_cost_cents),
        "by_queue": _count_by(fills, "queue_name", key_name="queue_name"),
        "by_strategy": _count_by(fills, "strategy_id", key_name="strategy_id"),
    }


def _probability_from_fill(fill: Dict[str, Any]) -> Optional[float]:
    value = _safe_float(fill.get("p_lcb") or fill.get("p_model") or fill.get("model_probability"))
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def _brier_value(probability: float, payout: float) -> float:
    outcome = 1.0 if payout >= 0.999 else 0.0
    return (float(probability) - outcome) ** 2


def _log_loss_value(probability: float, payout: float) -> float:
    outcome = 1.0 if payout >= 0.999 else 0.0
    clipped = min(1.0 - 1e-6, max(1e-6, float(probability)))
    return -(outcome * math.log(clipped) + (1.0 - outcome) * math.log(1.0 - clipped))


def _fill_group_key(fill: Dict[str, Any], field: str) -> str:
    if field == "strategy_bucket":
        return f"{fill.get('strategy_id') or 'unknown'}|{fill.get('bucket_type') or 'unknown'}"
    if field == "price_bucket":
        entry_price = _safe_float(fill.get("entry_price"))
        if entry_price is None:
            return "price_unknown"
        if float(entry_price) < 0.005:
            return "price_lt_0_005"
        if float(entry_price) < 0.03:
            return "price_0_005_to_0_03"
        return "price_ge_0_03"
    return str(fill.get(field) or "unknown")


def _performance_by_field(fills: Iterable[Dict[str, Any]], field: str, *, key_name: str) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        key = _fill_group_key(fill, field)
        row = groups.setdefault(
            key,
            {
                key_name: key,
                "fill_count": 0,
                "fully_filled_count": 0,
                "missed_fill_count": 0,
                "resolved_count": 0,
                "win_count": 0,
                "pnl_values": [],
                "entry_prices": [],
                "q_effective_values": [],
                "ev_safe_values": [],
                "brier_values": [],
                "log_loss_values": [],
            },
        )
        row["fill_count"] += 1
        if fill.get("fully_filled") is True:
            row["fully_filled_count"] += 1
        if fill.get("missed_fill") is True:
            row["missed_fill_count"] += 1
        pnl = _safe_float(fill.get("pnl_cents"))
        if pnl is not None:
            row["resolved_count"] += 1
            row["pnl_values"].append(float(pnl))
            if pnl > 0:
                row["win_count"] += 1
        entry_price = _safe_float(fill.get("entry_price"))
        if entry_price is not None:
            row["entry_prices"].append(float(entry_price))
        q_effective = _safe_float(fill.get("q_effective"))
        if q_effective is not None:
            row["q_effective_values"].append(float(q_effective))
        ev_safe = _safe_float(fill.get("ev_safe"))
        if ev_safe is not None:
            row["ev_safe_values"].append(float(ev_safe))
        payout = _safe_float(fill.get("payout"))
        probability = _probability_from_fill(fill)
        if payout is not None and probability is not None:
            row["brier_values"].append(_brier_value(probability, payout))
            row["log_loss_values"].append(_log_loss_value(probability, payout))

    rows: List[Dict[str, Any]] = []
    for row in groups.values():
        pnl_values = row.pop("pnl_values")
        entry_prices = row.pop("entry_prices")
        q_effective_values = row.pop("q_effective_values")
        ev_safe_values = row.pop("ev_safe_values")
        brier_values = row.pop("brier_values")
        log_loss_values = row.pop("log_loss_values")
        resolved_count = int(row["resolved_count"])
        fill_count = int(row["fill_count"])
        row["resolved_coverage"] = round(resolved_count / fill_count, 6) if fill_count else None
        row["win_rate"] = round(int(row["win_count"]) / resolved_count, 6) if resolved_count else None
        row["resolved_pnl_cents"] = round(sum(pnl_values), 6) if pnl_values else None
        row["mean_resolved_pnl_cents"] = _mean(pnl_values)
        row["mean_entry_price"] = _mean(entry_prices)
        row["mean_q_effective"] = _mean(q_effective_values)
        row["mean_ev_safe"] = _mean(ev_safe_values)
        row["brier_score"] = _mean(brier_values)
        row["log_loss"] = _mean(log_loss_values)
        rows.append(row)

    return sorted(
        rows,
        key=lambda row: (
            -int(row.get("resolved_count") or 0),
            -int(row.get("fill_count") or 0),
            str(row.get(key_name) or ""),
        ),
    )


def _performance_summary(replay: Dict[str, Any]) -> Dict[str, Any]:
    fills = [row for row in replay.get("fills") or [] if isinstance(row, dict)]
    by_price_bucket = _performance_by_field(
        fills,
        "price_bucket",
        key_name="price_bucket",
    )
    for row in by_price_bucket:
        row["live_gate_excluded"] = row.get("price_bucket") == "price_lt_0_005"
        row["live_gate_excluded_reason"] = (
            "dust_price_bucket_diagnostic_only"
            if row.get("live_gate_excluded")
            else None
        )
    return {
        "schema_version": "polyweather_weather_strict_gate_replay_performance.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "fill_count": len(fills),
        "by_strategy": _performance_by_field(fills, "strategy_id", key_name="strategy_id"),
        "by_queue": _performance_by_field(fills, "queue_name", key_name="queue_name"),
        "by_bucket_type": _performance_by_field(fills, "bucket_type", key_name="bucket_type"),
        "by_city": _performance_by_field(fills, "city", key_name="city"),
        "by_strategy_bucket": _performance_by_field(
            fills,
            "strategy_bucket",
            key_name="strategy_bucket",
        ),
        "by_price_bucket": by_price_bucket,
    }


def _ev_audit_summary(replay: Dict[str, Any]) -> Dict[str, Any]:
    fills = [row for row in replay.get("fills") or [] if isinstance(row, dict)]
    resolved = [
        row
        for row in fills
        if _safe_float(row.get("pnl_cents")) is not None
        and _safe_float(row.get("payout")) is not None
    ]
    positive_ev_negative_pnl = [
        row
        for row in resolved
        if (_safe_float(row.get("ev_safe")) or 0.0) > 0.0
        and (_safe_float(row.get("pnl_cents")) or 0.0) < 0.0
    ]
    fill_count = len(fills)
    resolved_fill_count = len(resolved)
    return {
        "schema_version": "polyweather_weather_strict_gate_replay_ev_audit.v1",
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "fill_count": fill_count,
        "resolved_fill_count": resolved_fill_count,
        "resolved_fill_coverage": (
            round(resolved_fill_count / fill_count, 6)
            if fill_count
            else None
        ),
        "resolved_pnl_cents": replay.get("resolved_pnl_cents"),
        "brier_score": replay.get("brier_score"),
        "log_loss": replay.get("log_loss"),
        "positive_ev_safe_but_negative_pnl_count": len(positive_ev_negative_pnl),
        "positive_ev_safe_but_negative_pnl_samples": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "strategy_id": row.get("strategy_id"),
                "bucket_type": row.get("bucket_type"),
                "entry_price": row.get("entry_price"),
                "payout": row.get("payout"),
                "pnl_cents": row.get("pnl_cents"),
                "ev_safe": row.get("ev_safe"),
            }
            for row in positive_ev_negative_pnl[:10]
        ],
    }


def _resolution_source_counts(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _count_by(records, "resolution_record_source", key_name="resolution_record_source")


def _parse_utc(value: Any) -> Optional[datetime]:
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


def _visible_at_or_before(row: Dict[str, Any], replay_time: str, *fields: str) -> bool:
    replay_dt = _parse_utc(replay_time)
    if replay_dt is None:
        return True
    for field in fields:
        parsed = _parse_utc(row.get(field))
        if parsed is not None:
            return parsed <= replay_dt
    return True


def _visible_orderbooks_by_token(
    orderbook_rows: Iterable[Dict[str, Any]],
    *,
    replay_time: str,
) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    latest_time: Dict[str, str] = {}
    for row in orderbook_rows:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "").strip()
        recorded_at = str(row.get("recorded_at") or row.get("available_at") or "")
        if not token_id or not _visible_at_or_before(row, replay_time, "recorded_at", "available_at"):
            continue
        if token_id not in latest_time or recorded_at > latest_time[token_id]:
            latest[token_id] = row
            latest_time[token_id] = recorded_at
    return latest


def _has_ask_ladder(orderbook: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(orderbook, dict):
        return False
    asks = orderbook.get("ask_ladder") or orderbook.get("asks")
    return isinstance(asks, list) and bool(asks)


def _sample_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "queue_record_id",
            "row_id",
            "id",
            "queue_name",
            "strategy_id",
            "market_slug",
            "token_id",
            "side",
            "available_at",
            "generated_at",
            "recorded_at",
            "bucket_type",
            "price",
            "ask",
            "ev_safe",
        )
        if row.get(key) is not None
    }


def _no_fill_diagnostics(
    *,
    queue_rows: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    orderbook_rows: List[Dict[str, Any]],
    resolved_rows: List[Dict[str, Any]],
    replay: Dict[str, Any],
    replay_time: str,
) -> Dict[str, Any]:
    visible_books = _visible_orderbooks_by_token(orderbook_rows, replay_time=replay_time)
    queue_with_token = [row for row in queue_rows if str(row.get("token_id") or "").strip()]
    missing_token_rows = [row for row in queue_rows if not str(row.get("token_id") or "").strip()]
    visible_candidates: List[Dict[str, Any]] = []
    missing_ask_ladder_rows: List[Dict[str, Any]] = []
    future_candidates: List[Dict[str, Any]] = []
    no_visible_candidates: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not _visible_at_or_before(candidate, replay_time, "generated_at", "recorded_at", "available_at"):
            future_candidates.append(candidate)
            continue
        token_id = str(candidate.get("token_id") or "").strip()
        orderbook = visible_books.get(token_id)
        if orderbook is None:
            no_visible_candidates.append(candidate)
            continue
        visible_candidates.append(candidate)
        if not _has_ask_ladder(orderbook):
            missing_ask_ladder_rows.append(candidate)

    reasons: List[Dict[str, Any]] = []
    if not queue_rows:
        reasons.append({"reason": "strict_gate_queue_missing", "count": 1})
    if queue_rows and not candidates:
        reasons.append(
            {
                "reason": "no_tokenized_replay_candidates",
                "count": max(1, len(missing_token_rows)),
            }
        )
    fill_count = int(replay.get("fill_count") or 0)
    if fill_count <= 0 and candidates:
        reason_counts = {
            "future_candidate": int(replay.get("skipped_future_candidate_count") or len(future_candidates)),
            "no_visible_orderbook": int(replay.get("no_visible_orderbook_count") or len(no_visible_candidates)),
            "missing_ask_ladder": len(missing_ask_ladder_rows),
            "depth_insufficient": int(replay.get("missed_fill_count") or 0),
        }
        for reason, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0])):
            if count > 0:
                reasons.append({"reason": reason, "count": count})
    return {
        "schema_version": "polyweather_weather_strict_gate_replay_no_fill_diagnostics.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "queue_record_count": len(queue_rows),
        "replay_candidate_count": len(candidates),
        "orderbook_snapshot_count": len(orderbook_rows),
        "resolved_outcome_count": len(resolved_rows),
        "queue_with_token_count": len(queue_with_token),
        "candidate_with_visible_orderbook_count": len(visible_candidates),
        "no_visible_orderbook_count": int(replay.get("no_visible_orderbook_count") or 0),
        "skipped_future_candidate_count": int(replay.get("skipped_future_candidate_count") or 0),
        "missed_fill_count": int(replay.get("missed_fill_count") or 0),
        "missing_ask_ladder_count": len(missing_ask_ladder_rows),
        "missing_token_id_count": len(missing_token_rows),
        "top_no_fill_reasons": reasons[:10],
        "sample_rows": {
            "queue_missing_token": [_sample_row(row) for row in missing_token_rows[:5]],
            "future_candidate": [_sample_row(row) for row in future_candidates[:5]],
            "no_visible_orderbook": [_sample_row(row) for row in no_visible_candidates[:5]],
            "missing_ask_ladder": [_sample_row(row) for row in missing_ask_ladder_rows[:5]],
        },
    }


def _official_final_value_samples(records: Iterable[Dict[str, Any]], *, limit: int = 10) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("official_final_value") is None:
            continue
        key = (
            str(record.get("market_id") or record.get("market_slug") or ""),
            str(record.get("official_final_value") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        samples.append(
            {
                "market_id": record.get("market_id"),
                "market_slug": record.get("market_slug"),
                "official_final_value": record.get("official_final_value"),
                "resolution_record_source": record.get("resolution_record_source"),
                "resolution_source": record.get("resolution_source"),
            }
        )
        if len(samples) >= max(0, int(limit)):
            break
    return samples


def _hard_conclusion(report: Dict[str, Any]) -> str:
    if int(report.get("queue_record_count") or 0) <= 0:
        return "strict_gate_replay_no_queue_records"
    if int(report.get("replay_candidate_count") or 0) <= 0:
        return "strict_gate_replay_no_tokenized_candidates"
    replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
    if int(replay.get("no_visible_orderbook_count") or 0) > 0:
        return "strict_gate_replay_needs_orderbook_archive"
    if int(replay.get("missed_fill_count") or 0) > 0:
        return "strict_gate_replay_execution_depth_insufficient"
    if int(replay.get("missing_resolution_count") or 0) > 0:
        return "strict_gate_replay_needs_resolved_outcomes"
    replay_pnl_cents = _safe_float(replay.get("resolved_pnl_cents"))
    if replay_pnl_cents is None:
        return "strict_gate_replay_needs_resolved_pnl"
    if replay_pnl_cents < 0.0:
        return "strict_gate_replay_negative_resolved_pnl"
    return "strict_gate_replay_ready_for_ev_audit"


def build_strict_gate_replay_report(
    *,
    queue_records: Iterable[Dict[str, Any]],
    orderbook_snapshots: Iterable[Dict[str, Any]],
    resolved_outcomes: Iterable[Dict[str, Any]],
    replay_time: Optional[str] = None,
    size: float = 1.0,
    queue_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    replay_time = replay_time or utc_now_iso()
    queue_rows = [row for row in queue_records if isinstance(row, dict)]
    orderbook_rows = [row for row in orderbook_snapshots if isinstance(row, dict)]
    resolved_rows = [row for row in resolved_outcomes if isinstance(row, dict)]
    candidates = strict_gate_queue_records_as_replay_candidates(
        queue_rows,
        queue_names=queue_names,
    )
    replay = replay_taker_candidates(
        candidates=candidates,
        orderbook_snapshots=orderbook_rows,
        resolved_outcomes=resolved_rows,
        replay_time=replay_time,
        size=size,
    )
    performance_summary = _performance_summary(replay)
    ev_audit_summary = _ev_audit_summary(replay)
    no_fill_diagnostics = _no_fill_diagnostics(
        queue_rows=queue_rows,
        candidates=candidates,
        orderbook_rows=orderbook_rows,
        resolved_rows=resolved_rows,
        replay=replay,
        replay_time=replay_time,
    )
    report = {
        "schema_version": STRICT_GATE_REPLAY_SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "replay_time": replay_time,
        "size": float(size),
        "queue_record_count": len(queue_rows),
        "replay_candidate_count": len(candidates),
        "orderbook_snapshot_count": len(orderbook_rows),
        "resolved_outcome_count": len(resolved_rows),
        "resolved_outcome_source_counts": _resolution_source_counts(resolved_rows),
        "resolved_outcome_official_final_value_count": len(
            [row for row in resolved_rows if row.get("official_final_value") is not None]
        ),
        "resolved_outcome_official_final_value_samples": _official_final_value_samples(resolved_rows),
        "queue_summary": _queue_summary(queue_rows),
        "execution_summary": _execution_summary(replay),
        "performance_summary": performance_summary,
        "ev_audit_summary": ev_audit_summary,
        "no_fill_diagnostics": no_fill_diagnostics,
        "resolved_fill_count": ev_audit_summary.get("resolved_fill_count"),
        "resolved_fill_coverage": ev_audit_summary.get("resolved_fill_coverage"),
        "positive_ev_safe_but_negative_pnl_count": ev_audit_summary.get(
            "positive_ev_safe_but_negative_pnl_count"
        ),
        "by_strategy_bucket": performance_summary.get("by_strategy_bucket") or [],
        "by_price_bucket": performance_summary.get("by_price_bucket") or [],
        "replay": replay,
    }
    report["hard_conclusion"] = _hard_conclusion(report)
    return report


def build_strict_gate_replay_report_from_dirs(
    *,
    queue_dir: str | Path = DEFAULT_STRICT_GATE_QUEUE_DIR,
    orderbook_archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    official_value_supplements_path: Optional[str | Path] = None,
    replay_time: Optional[str] = None,
    size: float = 1.0,
    queue_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    queue_root = Path(queue_dir)
    orderbook_root = Path(orderbook_archive_dir)
    journal_root = Path(journal_dir)
    backfill_root = Path(backfill_dir)
    return build_strict_gate_replay_report(
        queue_records=load_jsonl(queue_root / "strict_gate_queue.jsonl"),
        orderbook_snapshots=load_jsonl(orderbook_root / "orderbook_snapshots.jsonl"),
        resolved_outcomes=resolved_outcomes_from_audits_and_backfill(
            audit_records=load_jsonl(journal_root / "resolved_audits.jsonl"),
            backfill_records=load_closed_backfill_records_with_snapshot_supplements(
                backfill_root,
                official_value_supplements_path=official_value_supplements_path,
            ),
        ),
        replay_time=replay_time,
        size=size,
        queue_names=queue_names,
    )
