from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_reward_dollarization.v1"
SHARE_SCENARIOS = {
    "0_1pct": 0.001,
    "0_5pct": 0.005,
    "1pct": 0.01,
    "2pct": 0.02,
    "5pct": 0.05,
    "9pct": 0.09,
}
DAILY_ALLOCATION_SCENARIOS = (1, 5, 10, 25, 50)
DEFAULT_SHARE_SCENARIO_DAILY_ALLOCATION = 10.0


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


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


def _percentile(values: Iterable[float], pct: float) -> Optional[float]:
    materialized = sorted(float(value) for value in values)
    if not materialized:
        return None
    index = min(len(materialized) - 1, max(0, int(round((len(materialized) - 1) * pct))))
    return round(materialized[index], 8)


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    materialized = [float(value) for value in values if value is not None]
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 8)


def _latest_by_quote(quote_updates: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in quote_updates:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        if not quote_id:
            continue
        timestamp = str(row.get("update_time") or row.get("generated_at") or "")
        current = str(latest.get(quote_id, {}).get("update_time") or latest.get(quote_id, {}).get("generated_at") or "")
        if timestamp >= current:
            latest[quote_id] = row
    return latest


def _updates_by_quote(quote_updates: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in quote_updates:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        if quote_id:
            grouped[quote_id].append(row)
    return grouped


def _share_rows_by_quote(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        if quote_id:
            output[quote_id] = row
    return output


def _allocation_by_slug(allocation_audit: Dict[str, Any], metadata_audit: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = []
    rows.extend(row for row in allocation_audit.get("rows") or [] if isinstance(row, dict))
    rows.extend(row for row in metadata_audit.get("rows") or [] if isinstance(row, dict))
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        slug = str(row.get("market_slug") or "")
        if not slug:
            continue
        existing = output.get(slug, {})
        allocation = row.get("reward_allocation_raw", row.get("reward_allocation"))
        if existing.get("reward_allocation_value") is None and allocation is not None:
            existing["reward_allocation_value"] = allocation
        for source, target in (
            ("reward_allocation_unit", "reward_allocation_unit"),
            ("reward_allocation_period", "reward_allocation_period"),
            ("total_market_q_score", "total_market_q_score"),
            ("min_incentive_size", "min_incentive_size"),
            ("max_incentive_spread", "max_incentive_spread"),
        ):
            if existing.get(target) is None and row.get(source) is not None:
                existing[target] = row.get(source)
        raw_fields = set(existing.get("raw_reward_fields_found") or [])
        raw_fields.update(row.get("raw_reward_fields_found") or row.get("fields_found") or row.get("raw_field_names_found") or [])
        existing["raw_reward_fields_found"] = sorted(raw_fields)
        output[slug] = existing
    return output


def _risk_rows_by_quote(risk_rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for row in risk_rows:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        horizon = str(row.get("horizon") or "")
        if quote_id and horizon:
            output[quote_id][horizon] = row
    return output


def _time_on_book_seconds(quote: Dict[str, Any], updates: List[Dict[str, Any]]) -> float:
    start = _parse_utc(quote.get("quote_start_time") or quote.get("entry_time"))
    update_times = [_parse_utc(row.get("update_time") or row.get("generated_at")) for row in updates]
    update_times = [row for row in update_times if row is not None]
    if start is None and update_times:
        start = min(update_times)
    end = max(update_times) if update_times else None
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def _scenario_reward_cents(*, allocation_dollars: float, share: float, time_fraction: float) -> float:
    return round(allocation_dollars * 100.0 * share * time_fraction, 8)


def _break_even_share(*, loss_cents: float, allocation_dollars: float, time_fraction: float) -> Optional[float]:
    denominator = allocation_dollars * 100.0 * time_fraction
    if denominator <= 0:
        return None
    return round(max(0.0, loss_cents) / denominator, 8)


def _break_even_daily_allocation(*, loss_cents: float, share: Optional[float], time_fraction: float) -> Optional[float]:
    if share is None or share <= 0 or time_fraction <= 0:
        return None
    return round(max(0.0, loss_cents) / 100.0 / share / time_fraction, 8)


def build_weather_lp_reward_dollarization_report(
    *,
    paper_quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
    reward_metadata_audit_report: Dict[str, Any],
    reward_share_estimator_report: Dict[str, Any],
    reward_vs_risk_report: Dict[str, Any],
    reward_allocation_audit_report: Dict[str, Any],
    reward_share_rows: Iterable[Dict[str, Any]] = (),
    reward_vs_risk_rows: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    quote_rows = [row for row in paper_quotes if isinstance(row, dict)]
    update_rows = [row for row in quote_updates if isinstance(row, dict)]
    latest_by_quote = _latest_by_quote(update_rows)
    updates_by_quote = _updates_by_quote(update_rows)
    share_by_quote = _share_rows_by_quote(reward_share_rows)
    allocation_by_slug = _allocation_by_slug(reward_allocation_audit_report, reward_metadata_audit_report)
    risk_by_quote = _risk_rows_by_quote(reward_vs_risk_rows)
    visible_p10 = reward_share_estimator_report.get("visible_reward_share_p10") or reward_share_estimator_report.get("p10_visible_share")
    visible_p50 = reward_share_estimator_report.get("visible_reward_share_median") or reward_share_estimator_report.get("median_our_visible_share_proxy")
    visible_p90 = reward_share_estimator_report.get("visible_reward_share_p90") or reward_share_estimator_report.get("p90_visible_share")
    rows: List[Dict[str, Any]] = []
    for quote in quote_rows:
        quote_id = str(quote.get("quote_id") or "")
        latest = latest_by_quote.get(quote_id, {})
        history = updates_by_quote.get(quote_id, [])
        share_row = share_by_quote.get(quote_id, {})
        slug = str(quote.get("market_slug") or "")
        allocation_row = allocation_by_slug.get(slug, {})
        quote_size = _safe_float(quote.get("quote_size") or quote.get("size")) or 0.0
        quote_price = _safe_float(quote.get("quote_price")) or 0.0
        time_on_book = _time_on_book_seconds(quote, history)
        time_fraction = time_on_book / 86400.0
        qualified_count = sum(1 for row in history if row.get("qualifies_for_reward") is True or row.get("reward_score_at_update") is not None)
        cumulative = _safe_float(latest.get("cumulative_reward_points_proxy")) or _safe_float(quote.get("cumulative_reward_points_proxy")) or 0.0
        visible_share = _safe_float(share_row.get("our_visible_reward_share_proxy"))
        if visible_share is None:
            visible_share = _safe_float(reward_share_estimator_report.get("visible_reward_share_median"))
        allocation_exact = _safe_float(allocation_row.get("reward_allocation_value"))
        total_q = _safe_float(allocation_row.get("total_market_q_score"))
        exact_available = allocation_exact is not None and total_q is not None and total_q > 0
        exact_reward = None
        if exact_available:
            # The official share is unavailable in this stack unless the official total Q score is present.
            our_q = _safe_float(share_row.get("our_q_min_proxy") or latest.get("q_min_proxy"))
            if our_q is not None:
                exact_reward = round(allocation_exact * 100.0 * min(1.0, max(0.0, our_q / total_q)) * time_fraction, 8)
        visible_proxy_reward = None
        if allocation_exact is not None and visible_share is not None:
            visible_proxy_reward = round(allocation_exact * 100.0 * visible_share * time_fraction, 8)
        risk_rows = risk_by_quote.get(quote_id, {})
        markouts = {
            horizon: _safe_float((risk_rows.get(horizon) or {}).get("markout_cents"))
            for horizon in ("5m", "15m", "1h", "6h", "24h", "current")
        }
        current_markout = markouts.get("current")
        observed_markout_cents = round(float(current_markout or 0.0) * quote_size, 8)
        stress_minus_1 = round(-1.0 * quote_size, 8)
        stress_minus_3 = round(-3.0 * quote_size, 8)
        stress_minus_5 = round(-5.0 * quote_size, 8)
        daily_alloc_for_observed = _break_even_daily_allocation(
            loss_cents=max(0.0, -observed_markout_cents),
            share=visible_share,
            time_fraction=time_fraction,
        )
        daily_alloc_for_minus_3 = _break_even_daily_allocation(
            loss_cents=max(0.0, -stress_minus_3),
            share=visible_share,
            time_fraction=time_fraction,
        )
        row = {
            "schema_version": f"{SCHEMA_VERSION}.row",
            "quote_id": quote_id,
            "market_slug": quote.get("market_slug"),
            "city": quote.get("city"),
            "station_code": quote.get("station_code"),
            "token_id": quote.get("token_id"),
            "side": quote.get("side"),
            "strategy_variant": quote.get("strategy_variant"),
            "quote_size": quote_size,
            "quote_price": quote_price,
            "time_on_book_seconds": round(time_on_book, 3),
            "time_fraction": round(time_fraction, 10),
            "qualifies_for_reward_update_count": qualified_count,
            "cumulative_reward_points_proxy": round(cumulative, 8),
            "our_visible_share_proxy": visible_share,
            "visible_share_p10": visible_p10,
            "visible_share_p50": visible_p50,
            "visible_share_p90": visible_p90,
            "reward_allocation_exact": allocation_exact,
            "reward_allocation_source": "official_field" if allocation_exact is not None else None,
            "total_market_q_score_available": total_q is not None,
            "exact_reward_cents_available": exact_reward is not None,
            "exact_reward_cents": exact_reward,
            "visible_proxy_reward_cents": visible_proxy_reward,
            "exact": bool(exact_reward is not None),
            "confidence": "exact_official_q_score" if exact_reward is not None else ("visible_orderbook_proxy" if visible_share is not None else "scenario_only"),
            "markout_5m_cents": markouts.get("5m"),
            "markout_15m_cents": markouts.get("15m"),
            "markout_1h_cents": markouts.get("1h"),
            "markout_6h_cents": markouts.get("6h"),
            "markout_24h_cents": markouts.get("24h"),
            "current_markout_cents": current_markout,
            "observed_markout_total_cents": observed_markout_cents,
            "stress_markout_minus_1c": stress_minus_1,
            "stress_markout_minus_3c": stress_minus_3,
            "stress_markout_minus_5c": stress_minus_5,
            "break_even_share_for_minus_1c": _break_even_share(loss_cents=max(0.0, -stress_minus_1), allocation_dollars=DEFAULT_SHARE_SCENARIO_DAILY_ALLOCATION, time_fraction=time_fraction),
            "break_even_share_for_minus_3c": _break_even_share(loss_cents=max(0.0, -stress_minus_3), allocation_dollars=DEFAULT_SHARE_SCENARIO_DAILY_ALLOCATION, time_fraction=time_fraction),
            "break_even_share_for_minus_5c": _break_even_share(loss_cents=max(0.0, -stress_minus_5), allocation_dollars=DEFAULT_SHARE_SCENARIO_DAILY_ALLOCATION, time_fraction=time_fraction),
            "break_even_daily_allocation_for_observed_markout": daily_alloc_for_observed,
            "break_even_daily_allocation_for_minus_3c_stress": daily_alloc_for_minus_3,
            "capital_at_risk_cents": round(quote_price * quote_size * 100.0, 8),
            "paper_only": True,
            "counts_for_live_gate": False,
            "live_order_path": False,
        }
        for label, share in SHARE_SCENARIOS.items():
            row[f"scenario_reward_cents_if_share_{label}"] = _scenario_reward_cents(
                allocation_dollars=DEFAULT_SHARE_SCENARIO_DAILY_ALLOCATION,
                share=share,
                time_fraction=time_fraction,
            )
        for allocation in DAILY_ALLOCATION_SCENARIOS:
            row[f"scenario_reward_cents_if_daily_allocation_{allocation}"] = _scenario_reward_cents(
                allocation_dollars=float(allocation),
                share=float(visible_share or 0.0),
                time_fraction=time_fraction,
            )
        rows.append(row)

    quote_update_count = len(update_rows)
    shares = [float(row.get("our_visible_share_proxy")) for row in rows if row.get("our_visible_share_proxy") is not None]
    observed_markout_total = round(sum(float(row.get("observed_markout_total_cents") or 0.0) for row in rows), 8)
    stress_minus_1_total = round(sum(float(row.get("stress_markout_minus_1c") or 0.0) for row in rows), 8)
    stress_minus_3_total = round(sum(float(row.get("stress_markout_minus_3c") or 0.0) for row in rows), 8)
    stress_minus_5_total = round(sum(float(row.get("stress_markout_minus_5c") or 0.0) for row in rows), 8)
    time_on_book_total_seconds = sum(float(row.get("time_on_book_seconds") or 0.0) for row in rows)
    break_even_minus_1 = [float(row.get("break_even_share_for_minus_1c")) for row in rows if row.get("break_even_share_for_minus_1c") is not None]
    break_even_minus_3 = [float(row.get("break_even_share_for_minus_3c")) for row in rows if row.get("break_even_share_for_minus_3c") is not None]
    break_even_minus_5 = [float(row.get("break_even_share_for_minus_5c")) for row in rows if row.get("break_even_share_for_minus_5c") is not None]
    break_even_allocations = [float(row.get("break_even_daily_allocation_for_minus_3c_stress")) for row in rows if row.get("break_even_daily_allocation_for_minus_3c_stress") is not None]
    scenario_totals_by_share = {
        label: round(sum(float(row.get(f"scenario_reward_cents_if_share_{label}") or 0.0) for row in rows), 8)
        for label in SHARE_SCENARIOS
    }
    scenario_totals_by_allocation = {
        str(allocation): round(sum(float(row.get(f"scenario_reward_cents_if_daily_allocation_{allocation}") or 0.0) for row in rows), 8)
        for allocation in DAILY_ALLOCATION_SCENARIOS
    }
    conclusion_parts: List[str] = []
    exact_count = len([row for row in rows if row.get("exact_reward_cents_available")])
    if exact_count == 0 and any(value > 0 for value in scenario_totals_by_allocation.values()):
        conclusion_parts.append("scenario_positive_exact_unavailable")
    if observed_markout_total > 0 and any(value > 0 for value in scenario_totals_by_allocation.values()):
        conclusion_parts.append("reward_adds_to_positive_markout_paper_only")
    median_break_even_share = _percentile(break_even_minus_3, 0.5)
    if median_break_even_share is not None and median_break_even_share <= 0.01:
        conclusion_parts.append("stress_resilient_at_1pct_share")
    elif median_break_even_share is not None and median_break_even_share > 0.05:
        conclusion_parts.append("fragile_requires_high_reward_share")
    if not conclusion_parts:
        conclusion_parts.append("scenario_unresolved_exact_unavailable")
    return {
        "schema_version": SCHEMA_VERSION,
        "quote_count": len(rows),
        "quote_update_count": quote_update_count,
        "qualified_update_count": sum(int(row.get("qualifies_for_reward_update_count") or 0) for row in rows),
        "cumulative_reward_points_proxy": round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in rows), 8),
        "exact_reward_cents_available_count": exact_count,
        "allocation_available_count": len([row for row in rows if row.get("reward_allocation_exact") is not None]),
        "visible_share_available_count": len(shares),
        "visible_reward_share_median": _percentile(shares, 0.5),
        "visible_reward_share_p10": _percentile(shares, 0.1),
        "visible_reward_share_p90": _percentile(shares, 0.9),
        "share_scenario_daily_allocation_assumption": DEFAULT_SHARE_SCENARIO_DAILY_ALLOCATION,
        "scenario_total_reward_if_0_1pct_share": scenario_totals_by_share["0_1pct"],
        "scenario_total_reward_if_0_5pct_share": scenario_totals_by_share["0_5pct"],
        "scenario_total_reward_if_1pct_share": scenario_totals_by_share["1pct"],
        "scenario_total_reward_if_2pct_share": scenario_totals_by_share["2pct"],
        "scenario_total_reward_if_5pct_share": scenario_totals_by_share["5pct"],
        "scenario_total_reward_if_9pct_share": scenario_totals_by_share["9pct"],
        "scenario_total_reward_if_daily_allocation_1": scenario_totals_by_allocation["1"],
        "scenario_total_reward_if_daily_allocation_5": scenario_totals_by_allocation["5"],
        "scenario_total_reward_if_daily_allocation_10": scenario_totals_by_allocation["10"],
        "scenario_total_reward_if_daily_allocation_25": scenario_totals_by_allocation["25"],
        "scenario_total_reward_if_daily_allocation_50": scenario_totals_by_allocation["50"],
        "observed_markout_total_cents": observed_markout_total,
        "stress_minus_1c_total_cents": stress_minus_1_total,
        "stress_minus_3c_total_cents": stress_minus_3_total,
        "stress_minus_5c_total_cents": stress_minus_5_total,
        "break_even_share_median": _percentile(break_even_minus_3, 0.5),
        "break_even_share_p90": _percentile(break_even_minus_3, 0.9),
        "break_even_share_minus_1c_median": _percentile(break_even_minus_1, 0.5),
        "break_even_share_minus_1c_p90": _percentile(break_even_minus_1, 0.9),
        "break_even_share_minus_3c_median": _percentile(break_even_minus_3, 0.5),
        "break_even_share_minus_3c_p90": _percentile(break_even_minus_3, 0.9),
        "break_even_share_minus_5c_median": _percentile(break_even_minus_5, 0.5),
        "break_even_share_minus_5c_p90": _percentile(break_even_minus_5, 0.9),
        "break_even_daily_allocation_median": _percentile(break_even_allocations, 0.5),
        "break_even_daily_allocation_p90": _percentile(break_even_allocations, 0.9),
        "time_on_book_total_seconds": round(time_on_book_total_seconds, 3),
        "time_on_book_total_hours": round(time_on_book_total_seconds / 3600.0, 8),
        "conclusion": "+".join(conclusion_parts),
        "exact": exact_count > 0,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
    }


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


__all__ = [
    "DAILY_ALLOCATION_SCENARIOS",
    "SCHEMA_VERSION",
    "SHARE_SCENARIOS",
    "build_weather_lp_reward_dollarization_report",
    "load_json",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
