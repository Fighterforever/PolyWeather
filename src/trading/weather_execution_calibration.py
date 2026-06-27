from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.trading.weather_paper_journal import (
    DEFAULT_PAPER_JOURNAL_DIR,
    _bucket_label_type,
    _latest_markouts_by_fill_id,
    _safe_float,
    load_jsonl,
    price_bucket,
    stable_json_hash,
    spread_bucket,
    utc_now_iso,
)


EXECUTION_CALIBRATION_SCHEMA_VERSION = "polyweather_weather_execution_calibration.v1"
DEFAULT_MAKER_FOCUS_JOURNAL_DIR = Path("data/trading/weather_maker_focus_paper")


def _mean(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _midpoint(left: Any, right: Any) -> Optional[float]:
    left_value = _safe_float(left)
    right_value = _safe_float(right)
    if left_value is None or right_value is None:
        return None
    return round((left_value + right_value) / 2.0, 8)


def _latest_records_by_fill_id(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        fill_id = str(record.get("fill_id") or f"row:{index}")
        latest[fill_id] = record
    return latest


def _execution_scenarios(
    fill: Dict[str, Any],
    markout: Dict[str, Any],
) -> List[Dict[str, Any]]:
    current_bid = _safe_float(markout.get("current_bid"))
    if current_bid is None:
        return []

    entry_price = _safe_float(markout.get("entry_price") or fill.get("entry_price"))
    entry_bid = _safe_float(markout.get("entry_bid") or fill.get("entry_bid"))
    entry_ask = _safe_float(markout.get("entry_ask") or fill.get("entry_ask") or entry_price)
    entry_mid = _midpoint(entry_bid, entry_ask)
    entry_spread = _safe_float(markout.get("entry_spread") or fill.get("entry_spread"))
    if entry_spread is None and entry_bid is not None and entry_ask is not None:
        entry_spread = round(entry_ask - entry_bid, 8)

    scenario_specs: List[Tuple[str, Optional[float], str, bool, bool]] = [
        (
            "taker_ask",
            entry_price,
            "executed_taker_or_crossed_spread",
            False,
            True,
        ),
        (
            "midpoint",
            entry_mid,
            "hypothetical_midpoint_fill",
            True,
            False,
        ),
        (
            "maker_bid",
            entry_bid,
            "hypothetical_resting_bid_fill",
            True,
            False,
        ),
    ]
    records: List[Dict[str, Any]] = []
    for execution_mode, scenario_entry, fill_assumption, hypothetical, mirrors_current in scenario_specs:
        if scenario_entry is None:
            continue
        markout_cents = round((current_bid - scenario_entry) * 100.0, 6)
        markout_pct = (
            round((current_bid / scenario_entry - 1.0) * 100.0, 6)
            if scenario_entry != 0
            else None
        )
        records.append(
            {
                "schema_version": EXECUTION_CALIBRATION_SCHEMA_VERSION,
                "fill_id": fill.get("fill_id") or markout.get("fill_id"),
                "markout_id": markout.get("markout_id"),
                "execution_mode": execution_mode,
                "fill_assumption": fill_assumption,
                "hypothetical_fill": hypothetical,
                "mirrors_current_markout": mirrors_current,
                "counts_for_live_gate": False,
                "paper_only": True,
                "city": markout.get("city") or fill.get("city"),
                "question": markout.get("question") or fill.get("question"),
                "market_slug": markout.get("market_slug") or fill.get("market_slug"),
                "token_id": markout.get("token_id") or fill.get("token_id"),
                "side": markout.get("side") or fill.get("side"),
                "outcome": markout.get("outcome") or fill.get("outcome"),
                "bucket_label": markout.get("bucket_label") or fill.get("bucket_label"),
                "bucket_type": markout.get("bucket_type")
                or _bucket_label_type(markout.get("bucket_label") or fill.get("bucket_label")),
                "markout_horizon": markout.get("markout_horizon") or "unknown",
                "entry_bid": entry_bid,
                "entry_ask": entry_ask,
                "entry_mid": entry_mid,
                "entry_spread": entry_spread,
                "entry_spread_cents": round(entry_spread * 100.0, 6) if entry_spread is not None else None,
                "scenario_entry_price": scenario_entry,
                "scenario_entry_price_bucket": price_bucket(scenario_entry),
                "entry_spread_bucket": markout.get("entry_spread_bucket") or spread_bucket(entry_spread),
                "current_bid": current_bid,
                "current_ask": _safe_float(markout.get("current_ask")),
                "exit_price": current_bid,
                "markout_cents": markout_cents,
                "markout_pct": markout_pct,
            }
        )
    return records


def build_execution_calibration_records(
    *,
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    latest_only: bool = True,
) -> List[Dict[str, Any]]:
    journal_root = Path(journal_dir)
    fills = load_jsonl(journal_root / "paper_fills.jsonl")
    fills_by_id = _latest_records_by_fill_id(fills)
    markouts = load_jsonl(journal_root / "markouts.jsonl")
    selected = _latest_markouts_by_fill_id(markouts) if latest_only else markouts

    records: List[Dict[str, Any]] = []
    for markout in selected:
        if not isinstance(markout, dict) or markout.get("status") != "marked":
            continue
        fill_id = str(markout.get("fill_id") or "")
        fill = fills_by_id.get(fill_id, {})
        records.extend(_execution_scenarios(fill, markout))
    return records


def summarize_execution_groups(
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
        markout = _safe_float(record.get("markout_cents"))
        if markout is None:
            continue
        key = tuple(str(record.get(field) or "unknown") for field in fields)
        groups.setdefault(key, []).append(record)

    rows: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) < max(1, int(min_count)):
            continue
        values = [float(record["markout_cents"]) for record in group]
        spread_values = [
            float(record["entry_spread_cents"])
            for record in group
            if _safe_float(record.get("entry_spread_cents")) is not None
        ]
        row = {field: key[index] for index, field in enumerate(fields)}
        row.update(
            {
                "count": len(values),
                "hypothetical_count": len([record for record in group if record.get("hypothetical_fill") is True]),
                "win_count": len([value for value in values if value > 0.0]),
                "win_rate": _ratio(len([value for value in values if value > 0.0]), len(values)),
                "mean_markout_cents": _mean(values),
                "min_markout_cents": round(min(values), 6),
                "max_markout_cents": round(max(values), 6),
                "mean_entry_spread_cents": _mean(spread_values),
            }
        )
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("execution_mode") or ""),
            float(row.get("mean_markout_cents") or 0.0),
        ),
    )


def _scenario_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("execution_mode")): row
        for row in rows
        if isinstance(row, dict) and row.get("execution_mode")
    }


def _execution_delta(by_execution_mode: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenarios = _scenario_index(by_execution_mode)
    taker = scenarios.get("taker_ask") or {}
    midpoint = scenarios.get("midpoint") or {}
    maker = scenarios.get("maker_bid") or {}
    taker_mean = _safe_float(taker.get("mean_markout_cents"))
    midpoint_mean = _safe_float(midpoint.get("mean_markout_cents"))
    maker_mean = _safe_float(maker.get("mean_markout_cents"))
    return {
        "taker_ask_mean_markout_cents": taker_mean,
        "midpoint_mean_markout_cents": midpoint_mean,
        "maker_bid_mean_markout_cents": maker_mean,
        "midpoint_improvement_vs_taker_cents": (
            round(midpoint_mean - taker_mean, 6)
            if midpoint_mean is not None and taker_mean is not None
            else None
        ),
        "maker_bid_improvement_vs_taker_cents": (
            round(maker_mean - taker_mean, 6)
            if maker_mean is not None and taker_mean is not None
            else None
        ),
        "spread_cost_explains_current_negative_markout": bool(
            taker_mean is not None
            and maker_mean is not None
            and taker_mean < 0.0
            and maker_mean >= 0.0
        ),
    }


def summarize_execution_calibration(
    journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    *,
    latest_only: bool = True,
    min_count: int = 1,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    records = build_execution_calibration_records(
        journal_dir=journal_dir,
        latest_only=latest_only,
    )
    by_execution_mode = summarize_execution_groups(
        records,
        group_fields=("execution_mode",),
        min_count=min_count,
    )
    by_execution_and_spread = summarize_execution_groups(
        records,
        group_fields=("execution_mode", "entry_spread_bucket"),
        min_count=min_count,
    )
    by_execution_and_city = summarize_execution_groups(
        records,
        group_fields=("execution_mode", "city"),
        min_count=min_count,
    )
    return {
        "schema_version": EXECUTION_CALIBRATION_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_iso(),
        "journal_dir": str(Path(journal_dir)),
        "latest_only": bool(latest_only),
        "live_gate_input": False,
        "notes": [
            "taker_ask mirrors the current conservative paper markout entry assumption.",
            "midpoint and maker_bid are hypothetical diagnostics and do not count for live readiness.",
        ],
        "record_count": len(records),
        "fill_count": len({str(record.get("fill_id")) for record in records if record.get("fill_id")}),
        "by_execution_mode": by_execution_mode,
        "by_execution_and_spread": by_execution_and_spread,
        "by_execution_and_city": by_execution_and_city,
        "execution_delta": _execution_delta(by_execution_mode),
    }


def _eligible_maker_focus_groups(
    execution_calibration: Dict[str, Any],
    *,
    group_fields: Iterable[str],
    min_count: int,
    min_mean_markout_cents: float,
    min_win_rate: float,
) -> List[Dict[str, Any]]:
    supported = {
        "entry_spread_bucket": "by_execution_and_spread",
        "city": "by_execution_and_city",
    }
    groups: List[Dict[str, Any]] = []
    for field in [str(value or "").strip() for value in group_fields if str(value or "").strip()]:
        source_key = supported.get(field)
        if not source_key:
            continue
        for row in execution_calibration.get(source_key) or []:
            if not isinstance(row, dict) or row.get("execution_mode") != "maker_bid":
                continue
            count = int(row.get("count") or 0)
            mean_markout = _safe_float(row.get("mean_markout_cents"))
            win_rate = _safe_float(row.get("win_rate"))
            value = str(row.get(field) or "").strip()
            if not value or mean_markout is None or win_rate is None:
                continue
            if (
                count >= max(1, int(min_count))
                and mean_markout >= float(min_mean_markout_cents)
                and win_rate >= float(min_win_rate)
            ):
                groups.append(
                    {
                        "group_field": field,
                        "group_value": value,
                        "source_group": source_key,
                        "execution_mode": "maker_bid",
                        "count": count,
                        "mean_markout_cents": mean_markout,
                        "win_rate": win_rate,
                        "min_markout_cents": _safe_float(row.get("min_markout_cents")),
                        "max_markout_cents": _safe_float(row.get("max_markout_cents")),
                        "hypothetical_count": int(row.get("hypothetical_count") or 0),
                    }
                )
    return sorted(
        groups,
        key=lambda row: (
            -int(row.get("count") or 0),
            -float(row.get("mean_markout_cents") or 0.0),
            str(row.get("group_field") or ""),
            str(row.get("group_value") or ""),
        ),
    )


def _maker_focus_value(item: Dict[str, Any], field: str) -> str:
    if field == "entry_spread_bucket":
        return spread_bucket(item.get("spread"))
    return str(item.get(field) or "").strip().lower()


def _maker_quote_risk_group(hit: str) -> str:
    text = str(hit or "").strip()
    prefix = "negative_markout_rule:"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return text.split(":", 1)[0]


def _maker_quote_risk_specificity(hit: str) -> str:
    group = _maker_quote_risk_group(hit)
    if "_and_" in group:
        return "specific"
    if group in {
        "maker_quote_by_market_family",
        "maker_quote_by_quarantine_reason",
        "maker_quote_by_quarantine_blocker_scope",
        "maker_quote_by_quote_strategy",
    }:
        return "broad"
    return "medium"


def _maker_quote_risk_breakdown(hits: Iterable[str]) -> Dict[str, List[str]]:
    breakdown = {"broad": [], "medium": [], "specific": []}
    for hit in hits:
        text = str(hit or "").strip()
        if not text:
            continue
        breakdown[_maker_quote_risk_specificity(text)].append(text)
    return {key: sorted(set(values)) for key, values in breakdown.items()}


def _maker_focus_suppression_counts(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    total = 0
    specific = 0
    medium_only = 0
    broad_only = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("maker_focus_suppression_reason") != "maker_quote_risk_rule_block":
            continue
        total += 1
        specific_hits = row.get("maker_focus_suppression_risk_hits_specific") or []
        medium_hits = row.get("maker_focus_suppression_risk_hits_medium") or []
        broad_hits = row.get("maker_focus_suppression_risk_hits_broad") or []
        if specific_hits:
            specific += 1
        elif medium_hits:
            medium_only += 1
        elif broad_hits:
            broad_only += 1
    return {
        "maker_quote_risk_suppressed_count": total,
        "maker_quote_specific_risk_suppressed_count": specific,
        "maker_quote_medium_only_risk_suppressed_count": medium_only,
        "maker_quote_broad_only_risk_suppressed_count": broad_only,
    }


def build_maker_focus_signal_report(
    signal_report: Dict[str, Any],
    execution_calibration: Dict[str, Any],
    *,
    group_fields: Iterable[str] = ("entry_spread_bucket",),
    min_group_count: int = 3,
    min_group_mean_markout_cents: float = 0.0,
    min_group_win_rate: float = 0.55,
    min_edge_percent: float = 5.0,
    source_sections: Iterable[str] = ("quarantine",),
    respect_maker_quote_risk_rules: bool = True,
    max_items: Optional[int] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Select current rows for maker-bid paper sampling when historical maker-only strata are positive."""

    generated_at = generated_at or utc_now_iso()
    eligible_groups = _eligible_maker_focus_groups(
        execution_calibration,
        group_fields=group_fields,
        min_count=min_group_count,
        min_mean_markout_cents=min_group_mean_markout_cents,
        min_win_rate=min_group_win_rate,
    )
    selected: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    sections = [str(section or "").strip() for section in source_sections if str(section or "").strip()]
    eligible_by_field: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for group in eligible_groups:
        eligible_by_field.setdefault(str(group["group_field"]), {})[str(group["group_value"]).lower()] = group

    for section in sections:
        for item in signal_report.get(section) or []:
            if not isinstance(item, dict):
                continue
            edge = _safe_float(item.get("edge_percent"))
            if edge is None or edge < float(min_edge_percent):
                continue
            bid = _safe_float(item.get("bid"))
            matching_groups: List[Dict[str, Any]] = []
            for field, values in eligible_by_field.items():
                value = _maker_focus_value(item, field).lower()
                if value in values:
                    matching_groups.append(values[value])
            if not matching_groups:
                continue
            maker_quote_risk_hits = [
                str(reason)
                for reason in item.get("risk_rule_hits") or []
                if str(reason).startswith("negative_markout_rule:maker_quote_")
            ]
            if respect_maker_quote_risk_rules and maker_quote_risk_hits:
                risk_breakdown = _maker_quote_risk_breakdown(maker_quote_risk_hits)
                suppressed.append(
                    {
                        **item,
                        "decision": "quarantine",
                        "maker_focus": False,
                        "maker_focus_suppressed": True,
                        "maker_focus_suppression_reason": "maker_quote_risk_rule_block",
                        "maker_focus_suppression_risk_hits": maker_quote_risk_hits,
                        "maker_focus_suppression_risk_hits_broad": risk_breakdown["broad"],
                        "maker_focus_suppression_risk_hits_medium": risk_breakdown["medium"],
                        "maker_focus_suppression_risk_hits_specific": risk_breakdown["specific"],
                        "maker_focus_groups": matching_groups,
                        "paper_only": True,
                        "live_gate_excluded": True,
                        "counts_for_live_gate": False,
                    }
                )
                continue
            if bid is None:
                suppressed.append(
                    {
                        **item,
                        "decision": "quarantine",
                        "maker_focus": False,
                        "maker_focus_suppressed": True,
                        "maker_focus_suppression_reason": "missing_bid_for_maker_entry",
                        "maker_focus_groups": matching_groups,
                        "paper_only": True,
                        "live_gate_excluded": True,
                        "counts_for_live_gate": False,
                    }
                )
                continue
            ask = _safe_float(item.get("ask") or item.get("price"))
            maker_item = {
                **item,
                "decision": "quarantine",
                "original_decision": item.get("original_decision") or item.get("decision"),
                "maker_focus": True,
                "maker_focus_entry_mode": "maker_bid",
                "maker_focus_reference_ask": ask,
                "maker_focus_reference_price": _safe_float(item.get("price")),
                "maker_focus_reference_spread": _safe_float(item.get("spread")),
                "maker_focus_groups": matching_groups,
                "maker_focus_reasons": [
                    f"maker_positive_stratum:{group['group_field']}={group['group_value']}"
                    for group in matching_groups
                ],
                "price": bid,
                "ask": ask,
                "bid": bid,
                "paper_only": True,
                "live_gate_excluded": True,
                "counts_for_live_gate": False,
            }
            selected.append(maker_item)

    selected = sorted(
        selected,
        key=lambda row: (
            -max(float(group.get("mean_markout_cents") or 0.0) for group in row.get("maker_focus_groups") or [{}]),
            -float(row.get("score") or 0.0),
        ),
    )
    if max_items is not None:
        selected = selected[: max(0, int(max_items))]
    suppression_counts = _maker_focus_suppression_counts(suppressed)
    identity = {
        "source_snapshot_id": signal_report.get("source_snapshot_id"),
        "eligible_groups": eligible_groups,
        "selected_keys": [
            {
                "market_slug": row.get("market_slug"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
                "price": row.get("price"),
            }
            for row in selected
        ],
    }
    return {
        "schema_version": EXECUTION_CALIBRATION_SCHEMA_VERSION,
        "report_type": "maker_focus_signal",
        "generated_at": generated_at,
        "source_snapshot_id": signal_report.get("source_snapshot_id"),
        "source_status": signal_report.get("source_status"),
        "source": signal_report.get("source"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "live_authorization_pct": 0,
        "maker_focus_id": stable_json_hash(identity, length=20),
        "selection_config": {
            "group_fields": [str(value) for value in group_fields],
            "min_group_count": max(1, int(min_group_count)),
            "min_group_mean_markout_cents": float(min_group_mean_markout_cents),
            "min_group_win_rate": float(min_group_win_rate),
            "min_edge_percent": float(min_edge_percent),
            "source_sections": sections,
            "respect_maker_quote_risk_rules": bool(respect_maker_quote_risk_rules),
            "max_items": max_items,
        },
        "eligible_group_count": len(eligible_groups),
        "eligible_groups": eligible_groups,
        "summary": {
            "candidate_count": 0,
            "watch_count": 0,
            "quarantine_count": len(selected),
            "maker_focus_count": len(selected),
            "maker_focus_suppressed_count": len(suppressed),
            **suppression_counts,
            "live_gate": False,
            "live_authorization_pct": 0,
            "live_blockers": [
                "maker_focus_paper_only",
                "maker_bid_fill_probability_unproven",
                "live_permission_false",
            ],
        },
        "candidates": [],
        "watch": [],
        "quarantine": selected,
        "suppressed": suppressed,
        "hard_conclusion": (
            "maker_focus_collect_formal_paper"
            if selected
            else (
                "maker_focus_current_matches_specific_risk_suppressed"
                if suppression_counts["maker_quote_specific_risk_suppressed_count"] > 0
                else (
                    "maker_focus_current_matches_suppressed"
                    if suppressed and eligible_groups
                    else (
                        "maker_focus_no_current_match"
                        if eligible_groups
                        else "maker_focus_no_positive_historical_stratum"
                    )
                )
            )
        ),
    }


def dump_execution_calibration(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
