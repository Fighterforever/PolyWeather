from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_maker_quote_journal import _latest_by_quote_id
from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _bucket_label_type,
    _latest_markouts_by_fill_id,
    _safe_float,
    _with_fill_context,
    load_jsonl,
    price_bucket,
    spread_bucket,
    utc_now_iso,
)
from src.trading.weather_quarantine_validation import DEFAULT_QUARANTINE_JOURNAL_DIR


QUARANTINE_SURFACE_SCHEMA_VERSION = "polyweather_weather_quarantine_surface.v1"


DEFAULT_GROUP_SPECS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("by_quarantine_reason", ("quarantine_reason",)),
    ("by_risk_rule_scope", ("risk_rule_scope_profile",)),
    ("by_city", ("city",)),
    ("by_side", ("side",)),
    ("by_bucket_type", ("bucket_type",)),
    ("by_city_and_bucket_type", ("city", "bucket_type")),
    ("by_side_and_bucket_type", ("side", "bucket_type")),
    ("by_city_and_side", ("city", "side")),
    ("by_entry_price_bucket", ("entry_price_bucket",)),
    ("by_entry_spread_bucket", ("entry_spread_bucket",)),
    ("by_markout_horizon", ("markout_horizon",)),
    ("by_horizon_and_spread", ("markout_horizon", "entry_spread_bucket")),
)
PROMOTABLE_GROUPS = {
    "by_city",
    "by_bucket_type",
    "by_city_and_bucket_type",
    "by_side_and_bucket_type",
    "by_city_and_side",
    "by_entry_price_bucket",
    "by_entry_spread_bucket",
}


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _risk_rule_group(reason: str) -> str:
    text = str(reason or "").strip()
    prefix = "negative_markout_rule:"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return text.split(":", 1)[0]


def _risk_rule_scope(reason: str) -> str:
    text = str(reason or "").strip()
    if not text.startswith("negative_markout_rule:"):
        return "non_risk"
    group = _risk_rule_group(text)
    dimensions = text.split(":", 2)[2] if text.count(":") >= 2 else ""
    if group in {
        "by_market_family",
        "by_quarantine_blocker_scope",
        "by_quarantine_reason",
        "maker_quote_by_market_family",
        "maker_quote_by_quarantine_blocker_scope",
        "maker_quote_by_quarantine_reason",
        "maker_quote_by_quote_strategy",
    }:
        return "broad"
    if "_and_" in group or "," in dimensions:
        return "specific"
    return "medium"


def _risk_scope_profile(row: Dict[str, Any]) -> str:
    scopes = sorted(
        {
            _risk_rule_scope(str(reason))
            for reason in row.get("risk_rule_hits") or []
            if str(reason).startswith("negative_markout_rule:")
        }
    )
    return "+".join(scopes) if scopes else "none"


def _latest_by_key(rows: Iterable[Dict[str, Any]], key_name: str) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = str(row.get(key_name) or f"row:{index}")
        latest[key] = row
    return latest


def _enriched_taker_records(quarantine_journal_dir: str | Path) -> List[Dict[str, Any]]:
    journal_root = Path(quarantine_journal_dir)
    fills = load_jsonl(journal_root / "paper_fills.jsonl")
    fills_by_id = {str(fill.get("fill_id")): fill for fill in fills if isinstance(fill, dict)}
    markouts = _latest_markouts_by_fill_id(load_jsonl(journal_root / "markouts.jsonl"))
    records: List[Dict[str, Any]] = []
    for markout in markouts:
        record = _with_fill_context(markout, fills_by_id)
        if record.get("status") != "marked" or _safe_float(record.get("markout_cents")) is None:
            continue
        fill = fills_by_id.get(str(record.get("fill_id") or "")) or {}
        record["risk_rule_scope_profile"] = _risk_scope_profile(fill)
        record["entry_price_bucket"] = record.get("entry_price_bucket") or price_bucket(record.get("entry_price"))
        record["entry_spread_bucket"] = record.get("entry_spread_bucket") or spread_bucket(record.get("entry_spread"))
        records.append(record)
    return records


def _enriched_maker_records(quarantine_journal_dir: str | Path) -> List[Dict[str, Any]]:
    journal_root = Path(quarantine_journal_dir)
    quotes_by_id = _latest_by_key(load_jsonl(journal_root / "maker_quotes.jsonl"), "quote_id")
    latest_markouts = _latest_by_quote_id(load_jsonl(journal_root / "maker_quote_markouts.jsonl"))
    records: List[Dict[str, Any]] = []
    for markout in latest_markouts:
        quote = quotes_by_id.get(str(markout.get("quote_id") or "")) or {}
        record = {**quote, **markout}
        if record.get("error"):
            continue
        record["risk_rule_scope_profile"] = _risk_scope_profile(quote)
        if not record.get("bucket_type"):
            record["bucket_type"] = _bucket_label_type(record.get("bucket_label"))
        if not record.get("entry_spread_bucket"):
            record["entry_spread_bucket"] = spread_bucket(record.get("entry_spread"))
        if not record.get("entry_price_bucket"):
            record["entry_price_bucket"] = price_bucket(record.get("entry_ask") or quote.get("entry_ask"))
        if not record.get("markout_horizon"):
            record["markout_horizon"] = record.get("quote_horizon") or "unknown"
        records.append(record)
    return records


def _group_key(record: Dict[str, Any], fields: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(str(record.get(field) or "unknown").strip().lower() or "unknown" for field in fields)


def _summarize_taker_group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [
        float(row["markout_cents"])
        for row in rows
        if _safe_float(row.get("markout_cents")) is not None
    ]
    return {
        "taker_marked_count": len(values),
        "taker_win_count": len([value for value in values if value > 0.0]),
        "taker_win_rate": _ratio(len([value for value in values if value > 0.0]), len(values)),
        "mean_taker_markout_cents": _mean(values),
        "min_taker_markout_cents": round(min(values), 6) if values else None,
        "max_taker_markout_cents": round(max(values), 6) if values else None,
    }


def _summarize_maker_group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    inferred = [row for row in rows if row.get("status") == "inferred_filled"]
    maker_values = [
        float(row["maker_markout_cents"])
        for row in inferred
        if _safe_float(row.get("maker_markout_cents")) is not None
    ]
    missed_values = [
        float(row["missed_taker_markout_cents"])
        for row in rows
        if _safe_float(row.get("missed_taker_markout_cents")) is not None
    ]
    return {
        "maker_quote_count": len(rows),
        "maker_inferred_fill_count": len(inferred),
        "maker_fill_inference_rate": _ratio(len(inferred), len(rows)),
        "maker_win_count": len([value for value in maker_values if value > 0.0]),
        "maker_win_rate": _ratio(len([value for value in maker_values if value > 0.0]), len(maker_values)),
        "mean_maker_markout_cents": _mean(maker_values),
        "mean_missed_taker_markout_cents": _mean(missed_values),
    }


def _failure_reasons(
    *,
    taker_marked_count: int,
    mean_taker_markout_cents: Optional[float],
    taker_win_rate: Optional[float],
    maker_quote_count: int,
    mean_maker_markout_cents: Optional[float],
    maker_win_rate: Optional[float],
    min_decision_count: int,
    min_promote_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
    min_maker_quote_count: int,
    min_maker_mean_markout_cents: float,
) -> List[str]:
    reasons: List[str] = []
    if taker_marked_count < max(1, int(min_promote_count)):
        reasons.append("taker_count_below_promote_threshold")
    if mean_taker_markout_cents is None:
        reasons.append("taker_mean_missing")
    elif mean_taker_markout_cents < float(min_mean_markout_cents):
        reasons.append("taker_mean_below_threshold")
    if taker_win_rate is None:
        reasons.append("taker_win_rate_missing")
    elif taker_win_rate < float(min_win_rate):
        reasons.append("taker_win_rate_below_threshold")
    if maker_quote_count < max(0, int(min_maker_quote_count)):
        reasons.append("maker_quote_count_below_threshold")
    elif mean_maker_markout_cents is not None and mean_maker_markout_cents < float(min_maker_mean_markout_cents):
        reasons.append("maker_mean_below_threshold")
    elif maker_win_rate is not None and maker_win_rate < float(min_win_rate):
        reasons.append("maker_win_rate_below_threshold")
    if taker_marked_count < max(1, int(min_decision_count)):
        reasons.append("collect_more_before_decision")
    return reasons


def _surface_action(
    *,
    taker_marked_count: int,
    mean_taker_markout_cents: Optional[float],
    taker_win_rate: Optional[float],
    maker_quote_count: int,
    mean_maker_markout_cents: Optional[float],
    maker_win_rate: Optional[float],
    min_decision_count: int,
    min_promote_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
    min_maker_quote_count: int,
    min_maker_mean_markout_cents: float,
) -> str:
    if taker_marked_count < max(1, int(min_decision_count)):
        return "continue_quarantine_sampling"
    maker_evidence_count = maker_quote_count >= max(1, int(min_maker_quote_count))
    maker_adverse = maker_evidence_count and (
        (
            mean_maker_markout_cents is not None
            and mean_maker_markout_cents < float(min_maker_mean_markout_cents)
        )
        or (
            maker_win_rate is not None
            and maker_win_rate < float(min_win_rate)
        )
    )
    taker_positive = (
        taker_marked_count >= max(1, int(min_promote_count))
        and mean_taker_markout_cents is not None
        and mean_taker_markout_cents >= float(min_mean_markout_cents)
        and taker_win_rate is not None
        and taker_win_rate >= float(min_win_rate)
        and not maker_adverse
    )
    if taker_positive:
        return "promote_to_formal_paper_review"
    maker_positive = (
        maker_evidence_count
        and
        mean_maker_markout_cents is not None
        and mean_maker_markout_cents >= float(min_mean_markout_cents)
        and maker_win_rate is not None
        and maker_win_rate >= float(min_win_rate)
    )
    if maker_positive:
        return "maker_only_watch"
    return "cooldown_exploration"


def _sort_group(row: Dict[str, Any]) -> Tuple[int, float, int, str]:
    action_rank = {
        "promote_to_formal_paper_review": 0,
        "maker_only_watch": 1,
        "continue_quarantine_sampling": 2,
        "cooldown_exploration": 3,
    }.get(str(row.get("action")), 9)
    mean = _safe_float(row.get("mean_taker_markout_cents"))
    return (
        action_rank,
        -(mean if mean is not None else -999.0),
        -int(row.get("taker_marked_count") or 0),
        str(row.get("group_name") or ""),
    )


def build_quarantine_surface_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    quarantine_journal_dir: str | Path = DEFAULT_QUARANTINE_JOURNAL_DIR,
    min_decision_count: int = 5,
    min_promote_count: int = 5,
    min_mean_markout_cents: float = 0.0,
    min_win_rate: float = 0.55,
    min_maker_quote_count: int = 0,
    min_maker_mean_markout_cents: float = 0.0,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    taker_records = _enriched_taker_records(quarantine_journal_dir)
    maker_records = _enriched_maker_records(quarantine_journal_dir)
    groups_by_name: Dict[str, List[Dict[str, Any]]] = {}

    for group_name, fields in DEFAULT_GROUP_SPECS:
        taker_groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        maker_groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        for record in taker_records:
            taker_groups.setdefault(_group_key(record, fields), []).append(record)
        for record in maker_records:
            maker_groups.setdefault(_group_key(record, fields), []).append(record)
        rows: List[Dict[str, Any]] = []
        for key in sorted(set(taker_groups) | set(maker_groups)):
            dimensions = {field: key[index] for index, field in enumerate(fields)}
            taker = _summarize_taker_group(taker_groups.get(key) or [])
            maker = _summarize_maker_group(maker_groups.get(key) or [])
            action = _surface_action(
                taker_marked_count=int(taker["taker_marked_count"]),
                mean_taker_markout_cents=_safe_float(taker.get("mean_taker_markout_cents")),
                taker_win_rate=_safe_float(taker.get("taker_win_rate")),
                maker_quote_count=int(maker["maker_quote_count"]),
                mean_maker_markout_cents=_safe_float(maker.get("mean_maker_markout_cents")),
                maker_win_rate=_safe_float(maker.get("maker_win_rate")),
                min_decision_count=min_decision_count,
                min_promote_count=min_promote_count,
                min_mean_markout_cents=min_mean_markout_cents,
                min_win_rate=min_win_rate,
                min_maker_quote_count=min_maker_quote_count,
                min_maker_mean_markout_cents=min_maker_mean_markout_cents,
            )
            row = {
                "group_name": group_name,
                "dimensions": dimensions,
                **taker,
                **maker,
                "failure_reasons": _failure_reasons(
                    taker_marked_count=int(taker["taker_marked_count"]),
                    mean_taker_markout_cents=_safe_float(taker.get("mean_taker_markout_cents")),
                    taker_win_rate=_safe_float(taker.get("taker_win_rate")),
                    maker_quote_count=int(maker["maker_quote_count"]),
                    mean_maker_markout_cents=_safe_float(maker.get("mean_maker_markout_cents")),
                    maker_win_rate=_safe_float(maker.get("maker_win_rate")),
                    min_decision_count=min_decision_count,
                    min_promote_count=min_promote_count,
                    min_mean_markout_cents=min_mean_markout_cents,
                    min_win_rate=min_win_rate,
                    min_maker_quote_count=min_maker_quote_count,
                    min_maker_mean_markout_cents=min_maker_mean_markout_cents,
                ),
                "action": action,
                "counts_for_live_gate": False,
            }
            if action == "promote_to_formal_paper_review" and group_name not in PROMOTABLE_GROUPS:
                row["action"] = "continue_quarantine_sampling"
                row["failure_reasons"] = [
                    *row["failure_reasons"],
                    "non_promotable_diagnostic_group",
                ]
            rows.append(row)
        groups_by_name[group_name] = sorted(rows, key=_sort_group)

    all_groups = [row for rows in groups_by_name.values() for row in rows]
    action_counts: Dict[str, int] = {}
    for row in all_groups:
        action = str(row.get("action") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
    promote = [row for row in all_groups if row.get("action") == "promote_to_formal_paper_review"]
    maker_watch = [row for row in all_groups if row.get("action") == "maker_only_watch"]
    cooldown = [row for row in all_groups if row.get("action") == "cooldown_exploration"]
    collect_more = [row for row in all_groups if row.get("action") == "continue_quarantine_sampling"]
    return {
        "schema_version": QUARANTINE_SURFACE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "paper_only": True,
        "counts_for_live_gate": False,
        "paper_journal_dir": str(paper_journal_dir),
        "quarantine_journal_dir": str(quarantine_journal_dir),
        "thresholds": {
            "min_decision_count": max(1, int(min_decision_count)),
            "min_promote_count": max(1, int(min_promote_count)),
            "min_mean_markout_cents": float(min_mean_markout_cents),
            "min_win_rate": float(min_win_rate),
            "min_maker_quote_count": max(0, int(min_maker_quote_count)),
            "min_maker_mean_markout_cents": float(min_maker_mean_markout_cents),
        },
        "evidence": {
            "taker_record_count": len(taker_records),
            "maker_record_count": len(maker_records),
        },
        "action_counts": [
            {"action": action, "count": count}
            for action, count in sorted(action_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "promote_group_count": len(promote),
        "maker_only_watch_count": len(maker_watch),
        "cooldown_group_count": len(cooldown),
        "continue_sampling_group_count": len(collect_more),
        "promote_groups": sorted(promote, key=_sort_group)[:20],
        "maker_only_watch_groups": sorted(maker_watch, key=_sort_group)[:20],
        "cooldown_groups": sorted(cooldown, key=_sort_group)[:20],
        "continue_sampling_groups": sorted(collect_more, key=_sort_group)[:20],
        "groups": groups_by_name,
        "hard_conclusion": (
            "quarantine_surface_ready_for_formal_paper_review"
            if promote
            else (
                "quarantine_surface_has_maker_only_watch"
                if maker_watch
                else (
                    "quarantine_surface_currently_negative"
                    if cooldown
                    else "quarantine_surface_collect_more"
                )
            )
        ),
    }


def dump_quarantine_surface_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
