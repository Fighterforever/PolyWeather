from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_profitability_simulator.v1"
DAILY_REWARD_POOL_SCENARIOS = (1.0, 5.0, 10.0, 25.0, 50.0)
VISIBLE_SHARE_HAIRCUTS = (0.10, 0.25, 0.50, 1.00)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[Any]) -> Optional[float]:
    materialized = []
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            materialized.append(parsed)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 8)


def _percentile(values: Iterable[float], pct: float) -> Optional[float]:
    materialized = sorted(float(value) for value in values)
    if not materialized:
        return None
    index = min(len(materialized) - 1, max(0, int(round((len(materialized) - 1) * pct))))
    return round(materialized[index], 8)


def _latest_by_quote(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote_id = str(row.get("quote_id") or "")
        timestamp = str(row.get("update_time") or row.get("generated_at") or "")
        if quote_id and timestamp >= str(latest.get(quote_id, {}).get("update_time") or latest.get(quote_id, {}).get("generated_at") or ""):
            latest[quote_id] = row
    return latest


def _by_quote(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("quote_id"):
            output[str(row["quote_id"])] = row
    return output


def _reward_cents(*, daily_pool: float, visible_share: Optional[float], haircut: float, time_fraction: float) -> float:
    share = max(0.0, float(visible_share or 0.0) * haircut)
    return round(float(daily_pool) * 100.0 * share * max(0.0, time_fraction), 8)


def _roi(net_cents: float, capital_dollars: float) -> Optional[float]:
    denominator = capital_dollars * 100.0
    if denominator <= 0:
        return None
    return round(net_cents / denominator, 8)


def _bucket_cost(value: Any) -> str:
    parsed = _safe_float(value)
    if parsed is None:
        return "missing"
    if parsed < 0.80:
        return "<0.80"
    if parsed < 0.90:
        return "0.80-0.90"
    if parsed < 0.95:
        return "0.90-0.95"
    if parsed < 0.98:
        return "0.95-0.98"
    return ">=0.98"


def _group(rows: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "missing")].append(row)
    output: List[Dict[str, Any]] = []
    for key, bucket in sorted(grouped.items()):
        output.append(
            {
                field: key,
                "quote_count": len(bucket),
                "capital_locked_proxy": round(sum(float(row.get("capital_locked_proxy") or 0.0) for row in bucket), 8),
                "observed_markout_cents": round(sum(float(row.get("observed_markout_cents") or 0.0) for row in bucket), 8),
                "base_net_cents": round(sum(float((row.get("net_pnl_cents_by_scenario") or {}).get("base") or 0.0) for row in bucket), 8),
                "conservative_net_cents": round(sum(float((row.get("net_pnl_cents_by_scenario") or {}).get("conservative") or 0.0) for row in bucket), 8),
            }
        )
    return output


def build_weather_lp_profitability_simulation(
    *,
    paper_quotes: Iterable[Dict[str, Any]],
    quote_updates: Iterable[Dict[str, Any]],
    reward_vs_risk_report: Dict[str, Any],
    reward_share_estimator_report: Dict[str, Any],
    reward_dollarization_report: Dict[str, Any],
    reward_allocation_audit_report: Dict[str, Any],
    weather_lp_experiment_report: Dict[str, Any],
    reward_dollarization_rows: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    quote_rows = [row for row in paper_quotes if isinstance(row, dict)]
    update_rows = [row for row in quote_updates if isinstance(row, dict)]
    latest_updates = _latest_by_quote(update_rows)
    dollar_rows = _by_quote(reward_dollarization_rows)
    visible_median = _safe_float(reward_share_estimator_report.get("visible_reward_share_median") or reward_dollarization_report.get("visible_reward_share_median"))
    rows: List[Dict[str, Any]] = []
    for quote in quote_rows:
        quote_id = str(quote.get("quote_id") or "")
        dollar = dollar_rows.get(quote_id, {})
        latest = latest_updates.get(quote_id, {})
        quote_size = _safe_float(quote.get("quote_size") or quote.get("size") or dollar.get("quote_size")) or 0.0
        quote_price = _safe_float(quote.get("quote_price") or dollar.get("quote_price")) or 0.0
        capital = quote_price * quote_size
        time_fraction = _safe_float(dollar.get("time_fraction")) or 0.0
        visible_share = _safe_float(dollar.get("our_visible_share_proxy"))
        if visible_share is None:
            visible_share = visible_median
        observed_markout = _safe_float(dollar.get("observed_markout_total_cents"))
        if observed_markout is None:
            markout_cents = _safe_float(latest.get("markout_from_quote_price") or latest.get("price_markout_from_entry")) or 0.0
            observed_markout = round(markout_cents * quote_size, 8)
        reward_by_scenario = {
            "conservative": _reward_cents(daily_pool=1.0, visible_share=visible_share, haircut=0.10, time_fraction=time_fraction),
            "base": _reward_cents(daily_pool=10.0, visible_share=visible_share, haircut=0.25, time_fraction=time_fraction),
            "optimistic": _reward_cents(daily_pool=25.0, visible_share=visible_share, haircut=0.50, time_fraction=time_fraction),
            "visible_upper": _reward_cents(daily_pool=10.0, visible_share=visible_share, haircut=1.00, time_fraction=time_fraction),
        }
        stress_minus_1 = -1.0 * quote_size
        stress_minus_3 = -3.0 * quote_size
        stress_minus_5 = -5.0 * quote_size
        net_by_scenario = {
            "conservative": round(reward_by_scenario["conservative"] + stress_minus_3, 8),
            "base": round(reward_by_scenario["base"] + observed_markout, 8),
            "optimistic": round(reward_by_scenario["optimistic"] + observed_markout, 8),
            "visible_upper": round(reward_by_scenario["visible_upper"] + observed_markout, 8),
        }
        roi_by_scenario = {key: _roi(value, capital) for key, value in net_by_scenario.items()}
        break_even_reward_pool = None
        if visible_share and time_fraction > 0:
            break_even_reward_pool = round(max(0.0, -observed_markout) / 100.0 / visible_share / time_fraction, 8)
        break_even_haircut = None
        if visible_share and time_fraction > 0:
            denominator = 10.0 * 100.0 * visible_share * time_fraction
            break_even_haircut = round(max(0.0, -stress_minus_3) / denominator, 8) if denominator > 0 else None
        rows.append(
            {
                "schema_version": f"{SCHEMA_VERSION}.row",
                "quote_id": quote_id,
                "market_slug": quote.get("market_slug"),
                "city": quote.get("city"),
                "station_code": quote.get("station_code"),
                "side": quote.get("side"),
                "strategy_variant": quote.get("strategy_variant"),
                "quote_price": quote_price,
                "quote_size": quote_size,
                "notional_at_risk": round(capital, 8),
                "capital_locked_proxy": round(capital, 8),
                "time_on_book_hours": round((_safe_float(dollar.get("time_on_book_seconds")) or 0.0) / 3600.0, 8),
                "cumulative_reward_points_proxy": _safe_float(dollar.get("cumulative_reward_points_proxy") or latest.get("cumulative_reward_points_proxy")),
                "visible_reward_share_proxy": visible_share,
                "observed_markout_cents": observed_markout,
                "markout_5m": dollar.get("markout_5m_cents"),
                "markout_15m": dollar.get("markout_15m_cents"),
                "markout_1h": dollar.get("markout_1h_cents"),
                "markout_6h": dollar.get("markout_6h_cents"),
                "markout_24h": dollar.get("markout_24h_cents"),
                "current_markout": dollar.get("current_markout_cents"),
                "exact_reward_available": bool(dollar.get("exact_reward_cents_available")),
                "reward_allocation_if_available": dollar.get("reward_allocation_exact"),
                "visible_share_haircut_scenarios": {
                    "visible_share_x_0_10": round(float(visible_share or 0.0) * 0.10, 8),
                    "visible_share_x_0_25": round(float(visible_share or 0.0) * 0.25, 8),
                    "visible_share_x_0_50": round(float(visible_share or 0.0) * 0.50, 8),
                    "visible_share_x_1_00": round(float(visible_share or 0.0), 8),
                },
                "daily_reward_pool_scenarios": [1, 5, 10, 25, 50],
                "estimated_reward_cents_by_scenario": reward_by_scenario,
                "net_pnl_cents_by_scenario": net_by_scenario,
                "roi_on_capital_by_scenario": roi_by_scenario,
                "stress_net_pnl_minus_1c_markout": round(reward_by_scenario["base"] + stress_minus_1, 8),
                "stress_net_pnl_minus_3c_markout": round(reward_by_scenario["base"] + stress_minus_3, 8),
                "stress_net_pnl_minus_5c_markout": round(reward_by_scenario["base"] + stress_minus_5, 8),
                "break_even_reward_pool": break_even_reward_pool,
                "break_even_visible_share_haircut": break_even_haircut,
                "basket_total_cost": quote.get("basket_total_cost") or quote.get("basket_cost"),
                "basket_cost_bucket": _bucket_cost(quote.get("basket_total_cost") or quote.get("basket_cost")),
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    total_capital = round(sum(float(row.get("capital_locked_proxy") or 0.0) for row in rows), 8)
    scenario_net = {
        key: round(sum(float((row.get("net_pnl_cents_by_scenario") or {}).get(key) or 0.0) for row in rows), 8)
        for key in ("conservative", "base", "optimistic", "visible_upper")
    }
    scenario_reward = {
        key: round(sum(float((row.get("estimated_reward_cents_by_scenario") or {}).get(key) or 0.0) for row in rows), 8)
        for key in ("conservative", "base", "optimistic", "visible_upper")
    }
    roi_table = {key: _roi(value, total_capital) for key, value in scenario_net.items()}
    stress_table = {
        "minus_1c": round(sum(float(row.get("stress_net_pnl_minus_1c_markout") or 0.0) for row in rows), 8),
        "minus_3c": round(sum(float(row.get("stress_net_pnl_minus_3c_markout") or 0.0) for row in rows), 8),
        "minus_5c": round(sum(float(row.get("stress_net_pnl_minus_5c_markout") or 0.0) for row in rows), 8),
    }
    break_even_pool = [float(row.get("break_even_reward_pool")) for row in rows if row.get("break_even_reward_pool") is not None]
    break_even_haircut = [float(row.get("break_even_visible_share_haircut")) for row in rows if row.get("break_even_visible_share_haircut") is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "quote_count": len(rows),
        "unique_market_count": len({row.get("market_slug") for row in rows if row.get("market_slug")}),
        "total_time_on_book_hours": round(sum(float(row.get("time_on_book_hours") or 0.0) for row in rows), 8),
        "total_capital_locked_proxy": total_capital,
        "observed_markout_total_cents": round(sum(float(row.get("observed_markout_cents") or 0.0) for row in rows), 8),
        "cumulative_reward_points_proxy": round(sum(float(row.get("cumulative_reward_points_proxy") or 0.0) for row in rows), 8),
        "visible_reward_share_median": reward_share_estimator_report.get("visible_reward_share_median") or reward_dollarization_report.get("visible_reward_share_median"),
        "exact_reward_available": bool(
            reward_dollarization_report.get("exact")
            or reward_dollarization_report.get("exact_reward_available")
            or reward_dollarization_report.get("exact_reward_cents_available_count")
        ),
        "scenario_table": {
            "conservative_reward": scenario_reward["conservative"],
            "conservative_net": scenario_net["conservative"],
            "base_reward": scenario_reward["base"],
            "base_net": scenario_net["base"],
            "optimistic_reward": scenario_reward["optimistic"],
            "optimistic_net": scenario_net["optimistic"],
            "visible_upper_reward": scenario_reward["visible_upper"],
            "visible_upper_net": scenario_net["visible_upper"],
        },
        "roi_table": roi_table,
        "stress_table": stress_table,
        "break_even_summary": {
            "median_break_even_reward_pool": _percentile(break_even_pool, 0.5),
            "p90_break_even_reward_pool": _percentile(break_even_pool, 0.9),
            "median_break_even_share_haircut": _percentile(break_even_haircut, 0.5),
            "p90_break_even_share_haircut": _percentile(break_even_haircut, 0.9),
        },
        "by_city": _group(rows, "city"),
        "by_strategy_variant": _group(rows, "strategy_variant"),
        "by_basket_cost_bucket": _group(rows, "basket_cost_bucket"),
        "scenario_status": {
            "conservative_positive": scenario_net["conservative"] > 0,
            "base_positive": scenario_net["base"] > 0,
            "optimistic_positive": scenario_net["optimistic"] > 0,
            "visible_upper_positive": scenario_net["visible_upper"] > 0,
        },
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
    "SCHEMA_VERSION",
    "build_weather_lp_profitability_simulation",
    "load_json",
    "load_jsonl",
    "write_json",
    "write_jsonl",
]
