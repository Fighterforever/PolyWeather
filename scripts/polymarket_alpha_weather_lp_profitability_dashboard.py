#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_dataset import write_json  # noqa: E402
from src.trading.polymarket_alpha.weather_lp_reward_dollarization import DAILY_ALLOCATION_SCENARIOS, SHARE_SCENARIOS  # noqa: E402


def _load(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
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


def _sum(rows: Iterable[Dict[str, Any]], key: str) -> float:
    total = 0.0
    for row in rows:
        try:
            total += float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            pass
    return round(total, 8)


def _mean(values: Iterable[Any]) -> Optional[float]:
    materialized = []
    for value in values:
        try:
            if value is not None:
                materialized.append(float(value))
        except (TypeError, ValueError):
            pass
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 8)


def _scenario_table(rows: List[Dict[str, Any]], visible_share: Optional[float]) -> List[Dict[str, Any]]:
    shares = dict(SHARE_SCENARIOS)
    if visible_share is not None:
        shares["visible_median"] = float(visible_share)
    table: List[Dict[str, Any]] = []
    total_fraction = sum(float(row.get("time_fraction") or 0.0) for row in rows)
    for allocation in DAILY_ALLOCATION_SCENARIOS:
        table_row: Dict[str, Any] = {"daily_allocation_usd": allocation}
        for label, share in shares.items():
            cents = float(allocation) * 100.0 * float(share) * total_fraction
            table_row[label] = {"cents": round(cents, 8), "dollars": round(cents / 100.0, 8)}
        table.append(table_row)
    return table


def _break_even_share_table(rows: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    total_fraction = sum(float(row.get("time_fraction") or 0.0) for row in rows)
    stress_loss = abs(sum(float(row.get("stress_markout_minus_3c") or 0.0) for row in rows))
    output: Dict[str, Optional[float]] = {}
    for allocation in DAILY_ALLOCATION_SCENARIOS:
        denominator = float(allocation) * 100.0 * total_fraction
        output[f"share_needed_at_{allocation}_day"] = round(stress_loss / denominator, 8) if denominator > 0 else None
    return output


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
                "update_count": sum(int(row.get("qualifies_for_reward_update_count") or 0) for row in bucket),
                "reward_points_proxy": _sum(bucket, "cumulative_reward_points_proxy"),
                "markout": _mean(row.get("current_markout_cents") for row in bucket),
            }
        )
    return output


def build_dashboard(args: argparse.Namespace) -> Dict[str, Any]:
    dollar = _load(args.dollarization_report)
    rows = _load_jsonl(args.dollarization_rows)
    paper = _load(args.paper_cycle_report)
    updates = _load(args.quote_update_report)
    window = _load(args.reward_window_report)
    cancellation = _load(args.cancellation_policy_report)
    visible_share = dollar.get("visible_reward_share_median")
    observed = dollar.get("observed_markout_total_cents")
    scenario_daily_10 = dollar.get("scenario_total_reward_if_daily_allocation_10")
    if observed is not None and float(observed) >= 0 and scenario_daily_10 and float(scenario_daily_10) > 0:
        recommendation = "continue_weather_lp_paper"
    elif observed is not None and float(observed) >= 0:
        recommendation = "continue_collecting_allocation_data"
    else:
        recommendation = "tighten_or_pause"
    return {
        "schema_version": "polyweather_polymarket_alpha_weather_lp_profitability_dashboard.v1",
        "paper_quote_count": dollar.get("quote_count") or paper.get("paper_quote_count"),
        "quote_update_count": dollar.get("quote_update_count") or updates.get("quote_update_count"),
        "active_quote_count": paper.get("active_quote_count") or dollar.get("quote_count"),
        "time_on_book_total_hours": dollar.get("time_on_book_total_hours"),
        "visible_reward_share_median": visible_share,
        "cumulative_reward_points_proxy": dollar.get("cumulative_reward_points_proxy"),
        "exact_reward_available": bool(dollar.get("exact_reward_cents_available_count")),
        "scenario_reward_table": _scenario_table(rows, visible_share),
        "observed_markout_total_cents": observed,
        "stress_markout_table": {
            "0c": 0.0,
            "-1c": dollar.get("stress_minus_1c_total_cents"),
            "-3c": dollar.get("stress_minus_3c_total_cents"),
            "-5c": dollar.get("stress_minus_5c_total_cents"),
        },
        "break_even_summary": {
            **_break_even_share_table(rows),
            "break_even_share_minus_1c_median": dollar.get("break_even_share_minus_1c_median"),
            "break_even_share_minus_3c_median": dollar.get("break_even_share_minus_3c_median"),
            "break_even_share_minus_5c_median": dollar.get("break_even_share_minus_5c_median"),
            "break_even_daily_allocation_median": dollar.get("break_even_daily_allocation_median"),
            "break_even_daily_allocation_p90": dollar.get("break_even_daily_allocation_p90"),
        },
        "city_summary": _group(rows, "city"),
        "strategy_variant_summary": _group(rows, "strategy_variant"),
        "40_51_window_summary": {
            "window_confidence": window.get("window_confidence") or window.get("confidence"),
            "40_to_51_support_count": window.get("40_to_51_support_count"),
            "outside_window_support_count": window.get("outside_window_support_count"),
        },
        "cancellation_policy_summary": {
            "recommendation": cancellation.get("cancellation_policy_recommendation"),
            "best_policy": cancellation.get("best_policy"),
        },
        "recommendation": recommendation,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build human-readable Weather LP profitability dashboard.")
    parser.add_argument("--dollarization-report", default="evidence/weather_lp_rewards/reward_dollarization_report.json")
    parser.add_argument("--dollarization-rows", default="evidence/weather_lp_rewards/reward_dollarization_rows.jsonl")
    parser.add_argument("--paper-cycle-report", default="evidence/weather_lp_rewards/paper_cycle_report.json")
    parser.add_argument("--quote-update-report", default="evidence/weather_lp_rewards/paper_quote_update_report.json")
    parser.add_argument("--reward-window-report", default="evidence/weather_lp_rewards/reward_window_report.json")
    parser.add_argument("--cancellation-policy-report", default="evidence/weather_lp_rewards/cancellation_policy_report.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/weather_lp_profitability_dashboard.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    dashboard = build_dashboard(args)
    write_json(args.summary_output, dashboard)
    print(
        json.dumps(
            {
                "paper_quote_count": dashboard.get("paper_quote_count"),
                "observed_markout_total_cents": dashboard.get("observed_markout_total_cents"),
                "recommendation": dashboard.get("recommendation"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
