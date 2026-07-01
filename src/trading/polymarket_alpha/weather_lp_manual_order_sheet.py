from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_manual_order_sheet.v1"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_by_quote(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        when = str(row.get("update_time") or row.get("generated_at") or "")
        if quote_id and when >= str(latest.get(quote_id, {}).get("update_time") or latest.get(quote_id, {}).get("generated_at") or ""):
            latest[quote_id] = row
    return latest


def _by_quote(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("quote_id")): row for row in rows if isinstance(row, dict) and row.get("quote_id")}


def _by_market_token(rows: Iterable[Dict[str, Any]]) -> Dict[tuple[str, str], Dict[str, Any]]:
    indexed: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("market_slug") or ""), str(row.get("token_id") or ""))
        if key[0] and key[1]:
            indexed[key] = row
    return indexed


def build_weather_lp_manual_order_sheet(
    *,
    quote_optimizer_report: Dict[str, Any],
    reward_vs_risk_report: Dict[str, Any],
    profitability_simulation_report: Dict[str, Any],
    position_sizing_report: Dict[str, Any],
    kill_switch_policy_report: Dict[str, Any],
    paper_quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
    profitability_rows: Iterable[Dict[str, Any]],
    position_sizing_rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    quotes_by_id = _by_quote(paper_quotes)
    profit_by_id = _by_quote(profitability_rows)
    latest_updates = _latest_by_quote(quote_updates)
    kill_ready = bool(kill_switch_policy_report.get("cancellation_policy_ready"))
    rows: List[Dict[str, Any]] = []
    rejects: Counter[str] = Counter()
    for sizing in position_sizing_rows:
        if not isinstance(sizing, dict):
            continue
        quote_id = str(sizing.get("quote_id") or "")
        quote = quotes_by_id.get(quote_id, {})
        profit = profit_by_id.get(quote_id, {})
        latest = latest_updates.get(quote_id, {})
        reasons: List[str] = []
        if sizing.get("rejected"):
            reasons.append(str(sizing.get("reason") or "position_sizing_rejected"))
        if _safe_float(profit.get("observed_markout_cents")) is not None and float(profit.get("observed_markout_cents")) < 0:
            reasons.append("negative_observed_markout")
        if _safe_float(profit.get("visible_reward_share_proxy")) is None:
            reasons.append("missing_visible_reward_share_proxy")
        if (_safe_float(profit.get("cumulative_reward_points_proxy")) or 0.0) <= 0:
            reasons.append("non_positive_reward_score")
        if not kill_ready:
            reasons.append("missing_cancellation_policy")
        if _safe_float(sizing.get("recommended_paper_size")) is not None and float(sizing.get("recommended_paper_size")) <= 0:
            reasons.append("quote_size_exceeds_risk_cap")
        if _safe_float(profit.get("basket_total_cost")) is not None and float(profit.get("basket_total_cost")) >= 0.98:
            reasons.append("expensive_basket_near_full_payout")
        if _safe_float((profit.get("net_pnl_cents_by_scenario") or {}).get("conservative")) is not None and float((profit.get("net_pnl_cents_by_scenario") or {}).get("conservative")) < -1000:
            reasons.append("conservative_scenario_extremely_negative")
        if reasons:
            for reason in reasons:
                rejects[reason] += 1
            continue
        quote_price = _safe_float(quote.get("quote_price") or sizing.get("quote_price")) or 0.0
        midpoint = _safe_float(quote.get("midpoint") or quote.get("entry_midpoint") or latest.get("current_midpoint"))
        distance = abs(quote_price - midpoint) if midpoint is not None else None
        rows.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "market_slug": sizing.get("market_slug") or quote.get("market_slug"),
                "city": sizing.get("city") or quote.get("city"),
                "station_code": sizing.get("station_code") or quote.get("station_code"),
                "side": quote.get("side") or profit.get("side"),
                "token_id": quote.get("token_id"),
                "suggested_quote_price": quote_price,
                "suggested_quote_size": sizing.get("max_tiny_live_size_if_ever_allowed") or sizing.get("recommended_paper_size"),
                "min_incentive_size": quote.get("min_incentive_size"),
                "max_incentive_spread": quote.get("max_incentive_spread"),
                "quote_distance_from_midpoint": round(distance, 8) if distance is not None else None,
                "qualifies_for_reward": True,
                "visible_reward_share_proxy": profit.get("visible_reward_share_proxy"),
                "base_scenario_reward": (profit.get("estimated_reward_cents_by_scenario") or {}).get("base"),
                "conservative_scenario_reward": (profit.get("estimated_reward_cents_by_scenario") or {}).get("conservative"),
                "observed_markout_5m": profit.get("markout_5m"),
                "observed_markout_15m": profit.get("markout_15m"),
                "observed_markout_1h": profit.get("markout_1h"),
                "current_markout": profit.get("current_markout"),
                "max_loss_if_filled": sizing.get("max_loss_if_filled"),
                "capital_at_risk": sizing.get("max_loss_if_filled"),
                "basket_total_cost": sizing.get("basket_total_cost") or profit.get("basket_total_cost"),
                "city_regime": quote.get("city_regime"),
                "cancellation_rule": kill_switch_policy_report.get("recommended_action") or "paper_manual_cancel_rules_required",
                "manual_review_required": True,
                "do_not_auto_trade": True,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    total_capital = round(sum(float(row.get("capital_at_risk") or 0.0) for row in rows), 8)
    return {
        "schema_version": SCHEMA_VERSION,
        "suggested_manual_quote_count": len(rows),
        "rejected_candidate_count": sum(rejects.values()),
        "rejection_reasons": [{"reason": key, "count": value} for key, value in sorted(rejects.items())],
        "total_capital_at_risk_if_all_manual_quotes_used": total_capital,
        "recommended_max_total_capital": position_sizing_report.get("total_weather_lp_exposure_cap_dollars"),
        "recommended_quote_subset": rows[:10],
        "quote_optimizer_selected_count": quote_optimizer_report.get("selected_quote_count"),
        "base_scenario_net_cents": (profitability_simulation_report.get("scenario_table") or {}).get("base_net"),
        "conservative_scenario_net_cents": (profitability_simulation_report.get("scenario_table") or {}).get("conservative_net"),
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "rows": rows,
    }


def select_manual_payout_audit_quotes(
    *,
    manual_order_rows: Iterable[Dict[str, Any]],
    impact_rows: Iterable[Dict[str, Any]] = (),
    profitability_rows: Iterable[Dict[str, Any]] = (),
    max_quote_count: int = 3,
    max_total_capital_at_risk: float = 25.0,
) -> Dict[str, Any]:
    """Select a small manual audit set from paper-only manual review rows.

    This returns human-review rows only. It intentionally does not create an
    executable order payload or any API instruction.
    """

    impacts = _by_market_token(impact_rows)
    profits = _by_market_token(profitability_rows)
    selected: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    cumulative_capital = 0.0
    candidates: List[tuple[float, Dict[str, Any], List[str], Dict[str, Any], Dict[str, Any]]] = []
    for row in manual_order_rows:
        if not isinstance(row, dict):
            continue
        market_slug = str(row.get("market_slug") or "")
        token_id = str(row.get("token_id") or "")
        key = (market_slug, token_id)
        impact = impacts.get(key, {})
        profit = profits.get(key, {})
        reasons: List[str] = []
        quote_price = _safe_float(row.get("suggested_quote_price"))
        min_size = _safe_float(row.get("min_incentive_size"))
        original_size = _safe_float(row.get("suggested_quote_size"))
        audit_size = max(value for value in (min_size or 0.0, original_size or 0.0) if value is not None)
        capital = _safe_float(row.get("capital_at_risk"))
        if capital is None and quote_price is not None:
            capital = quote_price * audit_size
        current_markout = _safe_float(row.get("current_markout"))
        if current_markout is None:
            current_markout = _safe_float(profit.get("current_markout"))
        if current_markout is None:
            current_markout = _safe_float(profit.get("observed_markout_cents"))
        conservative_reward = _safe_float(row.get("conservative_scenario_reward"))
        conservative_net = _safe_float((profit.get("net_pnl_cents_by_scenario") or {}).get("conservative"))
        basket_total_cost = _safe_float(row.get("basket_total_cost"))
        distance = _safe_float(row.get("quote_distance_from_midpoint"))
        if not row.get("qualifies_for_reward"):
            reasons.append("not_reward_qualified")
        if min_size is None:
            reasons.append("missing_min_incentive_size")
        if quote_price is None:
            reasons.append("missing_quote_price")
        if _safe_float(row.get("max_incentive_spread")) is None:
            reasons.append("missing_max_incentive_spread")
        if _safe_float(row.get("visible_reward_share_proxy")) is None:
            reasons.append("missing_visible_reward_share_proxy")
        if current_markout is None or current_markout < 0:
            reasons.append("current_markout_negative_or_missing")
        if _safe_float(row.get("base_scenario_reward")) is None or float(row.get("base_scenario_reward") or 0.0) <= 0:
            reasons.append("base_scenario_not_positive")
        if conservative_reward is not None and conservative_reward < -100.0:
            reasons.append("conservative_reward_catastrophic")
        if conservative_net is not None and conservative_net < -100.0:
            reasons.append("conservative_net_catastrophic")
        if capital is None or capital <= 0:
            reasons.append("missing_capital_at_risk")
        if basket_total_cost is not None and basket_total_cost >= 0.98:
            reasons.append("expensive_basket_near_full_payout")
        if not impact:
            reasons.append("missing_impact_simulation")
        if impact and impact.get("quote_would_cross_or_take"):
            reasons.append("quote_would_cross_or_take")
        if impact and impact.get("quote_would_be_resting") is False:
            reasons.append("quote_would_not_be_resting")
        if impact and impact.get("impact_warning"):
            reasons.append(str(impact.get("impact_warning")))
        if not row.get("cancellation_rule"):
            reasons.append("missing_cancellation_rule")
        if str(row.get("city_regime") or "").lower() == "volatile":
            reasons.append("volatile_city_avoid_for_first_audit")
        if reasons:
            rejected.append(
                {
                    "market_slug": market_slug,
                    "token_id": token_id,
                    "city": row.get("city"),
                    "rejected_reason": list(dict.fromkeys(reasons)),
                    "manual_execution_only": True,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
            continue
        reward_score = _safe_float(row.get("visible_reward_share_proxy")) or 0.0
        risk_score = capital or 999.0
        distance_score = abs(distance or 0.0)
        score = (risk_score * 100.0) + (distance_score * 10.0) - reward_score
        candidates.append((score, row, [], impact, profit))
    for _, row, _, impact, profit in sorted(candidates, key=lambda item: item[0]):
        if len(selected) >= max_quote_count:
            rejected.append(
                {
                    "market_slug": row.get("market_slug"),
                    "token_id": row.get("token_id"),
                    "city": row.get("city"),
                    "rejected_reason": ["audit_quote_count_cap_reached"],
                    "manual_execution_only": True,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
            continue
        quote_price = _safe_float(row.get("suggested_quote_price")) or 0.0
        min_size = _safe_float(row.get("min_incentive_size")) or 0.0
        original_size = _safe_float(row.get("suggested_quote_size")) or 0.0
        audit_size = max(min_size, original_size)
        capital = _safe_float(row.get("capital_at_risk"))
        if capital is None:
            capital = quote_price * audit_size
        if cumulative_capital + float(capital or 0.0) > max_total_capital_at_risk:
            rejected.append(
                {
                    "market_slug": row.get("market_slug"),
                    "token_id": row.get("token_id"),
                    "city": row.get("city"),
                    "rejected_reason": ["audit_capital_cap_would_be_exceeded"],
                    "manual_execution_only": True,
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "live_order_path": False,
                }
            )
            continue
        current_markout = _safe_float(row.get("current_markout"))
        if current_markout is None:
            current_markout = _safe_float(profit.get("current_markout"))
        if current_markout is None:
            current_markout = _safe_float(profit.get("observed_markout_cents"))
        selected.append(
            {
                "rank": len(selected) + 1,
                "market_slug": row.get("market_slug"),
                "city": row.get("city"),
                "token_id": row.get("token_id"),
                "side": row.get("side"),
                "suggested_quote_price": quote_price,
                "suggested_quote_size": audit_size,
                "capital_at_risk": round(float(capital or 0.0), 8),
                "min_incentive_size": row.get("min_incentive_size"),
                "max_incentive_spread": row.get("max_incentive_spread"),
                "distance_from_midpoint": row.get("quote_distance_from_midpoint"),
                "visible_reward_share_proxy": row.get("visible_reward_share_proxy"),
                "expected_reward_low": row.get("conservative_scenario_reward"),
                "expected_reward_base": row.get("base_scenario_reward"),
                "expected_reward_high": (profit.get("estimated_reward_cents_by_scenario") or {}).get("optimistic"),
                "current_markout": current_markout,
                "cancel_rules": row.get("cancellation_rule"),
                "selected_reason": [
                    "reward_qualified",
                    "resting_quote",
                    "non_negative_markout",
                    "low_capital_first_audit",
                    "manual_ui_only",
                ],
                "quote_size_note": "audit_size_uses_min_incentive_size_when_needed",
                "manual_execution_only": True,
                "manual_review_required": True,
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
        cumulative_capital += float(capital or 0.0)
    return {
        "schema_version": f"{SCHEMA_VERSION}.manual_payout_audit_selector.v1",
        "selected_quote_count": len(selected),
        "total_selected_capital_at_risk": round(cumulative_capital, 8),
        "max_quote_count": max_quote_count,
        "max_total_capital_at_risk": max_total_capital_at_risk,
        "selected_quotes": selected,
        "rejected_quotes": rejected,
        "manual_execution_only": True,
        "manual_review_required": True,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def write_csv(path: str | Path, rows: Iterable[Dict[str, Any]]) -> int:
    materialized = [row for row in rows if isinstance(row, dict)]
    columns = [
        "market_slug",
        "city",
        "station_code",
        "side",
        "token_id",
        "suggested_quote_price",
        "suggested_quote_size",
        "capital_at_risk",
        "visible_reward_share_proxy",
        "base_scenario_reward",
        "conservative_scenario_reward",
        "cancellation_rule",
        "manual_review_required",
        "do_not_auto_trade",
        "live_order_path",
    ]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in materialized:
            writer.writerow({key: row.get(key) for key in columns})
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
    text = source.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if isinstance(parsed, dict):
        rows = parsed.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed_line = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed_line, dict):
            rows.append(parsed_line)
    return rows


__all__ = [
    "SCHEMA_VERSION",
    "build_weather_lp_manual_order_sheet",
    "load_json",
    "load_jsonl",
    "select_manual_payout_audit_quotes",
    "write_csv",
    "write_json",
    "write_jsonl",
]
