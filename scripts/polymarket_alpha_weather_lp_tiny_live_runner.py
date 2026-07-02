#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.live_safety import evaluate_live_safety, load_live_safety_config  # noqa: E402
from src.trading.polymarket_alpha.polymarket_live_client import PolymarketLiveClient, validate_credentials_present  # noqa: E402
from src.trading.polymarket_alpha.probability_dataset import write_json, write_jsonl  # noqa: E402
from src.trading.polymarket_alpha.weather_lp_tiny_live_selector import (  # noqa: E402
    build_weather_lp_tiny_live_selected_orders,
    load_jsonl_or_json,
    write_selected_orders_csv,
)


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_tiny_live_runner.v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_dict(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _noop_report(*, generated_at: str, reason: str, safety_config: Dict[str, Any], credentials: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "global_live_enabled": bool(safety_config.get("global_live_enabled")),
        "strategy_live_enabled": bool(safety_config.get("strategy_live_enabled")),
        "safety_passed": False,
        "noop_reason": reason,
        "selected_order_count": 0,
        "placed_order_count": 0,
        "canceled_order_count": 0,
        "open_order_count": 0,
        "total_capital_at_risk": 0.0,
        "max_capital_limit": safety_config.get("max_total_capital_usd"),
        "kill_switch_triggered": False,
        "credentials_present": credentials.get("credentials_present"),
        "missing_credential_keys": credentials.get("missing_credential_keys"),
        "paper_runner_unchanged": True,
        "live_order_path": False,
    }


def build_tiny_live_runner_report(args: argparse.Namespace, *, env: Dict[str, str] | None = None) -> Dict[str, Any]:
    env = env or dict(os.environ)
    generated_at = args.generated_at or utc_now_iso()
    config = load_live_safety_config(env)
    credentials = validate_credentials_present(env)
    if not config.get("global_live_enabled"):
        return _noop_report(generated_at=generated_at, reason="live_disabled", safety_config=config, credentials=credentials)
    if not config.get("strategy_live_enabled"):
        return _noop_report(generated_at=generated_at, reason="strategy_live_disabled", safety_config=config, credentials=credentials)
    if not credentials.get("credentials_present"):
        return _noop_report(generated_at=generated_at, reason="credentials_missing", safety_config=config, credentials=credentials)

    selected_quote_report = _load_dict(args.selected_quotes)
    selected_orders_report = build_weather_lp_tiny_live_selected_orders(
        selected_quote_report=selected_quote_report,
        impact_rows=load_jsonl_or_json(args.impact_rows),
        kill_switch_checklist=_load_dict(args.kill_switch),
        env=env,
        generated_at=generated_at,
    )
    write_json(args.selected_orders_output, selected_orders_report)
    write_selected_orders_csv(args.selected_orders_csv, selected_orders_report.get("selected_orders") or [])
    orders = [row for row in selected_orders_report.get("selected_orders") or [] if isinstance(row, dict)]
    client = PolymarketLiveClient(env=env, audit_log_path=args.orders_output, error_log_path=args.errors_output)
    open_orders = client.list_open_orders(strategy_id="weather_lp_reward")
    balance = client.get_balance()
    placed_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []
    blocker_counts: Counter[str] = Counter()
    total_capital = 0.0
    for index, order in enumerate(orders[: int(config.get("max_open_orders") or 3)], start=1):
        total_capital += float(order.get("capital_at_risk") or 0.0)
        safety = evaluate_live_safety(
            order=order,
            env=env,
            credentials_present_override=bool(credentials.get("credentials_present")),
            balance_available=bool(balance.get("balance_available")),
            wallet_balance_check_passed=bool(balance.get("wallet_balance_check_passed")),
            open_order_count=len(open_orders) + index,
            total_capital_usd=total_capital,
            per_market_capital_usd=float(order.get("capital_at_risk") or 0.0),
            per_city_capital_usd=float(order.get("capital_at_risk") or 0.0),
            selected_quote_allowlisted=True,
            reward_qualified=True,
            best_bid=order.get("best_bid"),
            best_ask=order.get("best_ask"),
        )
        result = client.place_gtd_limit_order(
            order,
            safety_report=safety,
            best_bid=order.get("best_bid"),
            best_ask=order.get("best_ask"),
            total_capital_usd=total_capital,
            per_market_capital_usd=float(order.get("capital_at_risk") or 0.0),
            per_city_capital_usd=float(order.get("capital_at_risk") or 0.0),
            open_order_count=len(open_orders) + index,
            selected_quote_allowlisted=True,
            reward_qualified=True,
        )
        if result.get("placed"):
            placed_rows.append({**order, "placement_result": result})
        else:
            for blocker in result.get("blockers") or ["placement_failed"]:
                blocker_counts[str(blocker)] += 1
            error_rows.append(result)
    if placed_rows:
        write_jsonl(args.orders_output, placed_rows)
    if error_rows:
        write_jsonl(args.errors_output, error_rows)
    updates: List[Dict[str, Any]] = []
    cancellations: List[Dict[str, Any]] = []
    write_jsonl(args.updates_output, updates)
    write_jsonl(args.cancellations_output, cancellations)
    placed_count = len(placed_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "global_live_enabled": True,
        "strategy_live_enabled": True,
        "safety_passed": placed_count > 0 and not blocker_counts,
        "noop_reason": None if placed_count else "no_orders_placed",
        "selected_order_count": selected_orders_report.get("selected_order_count"),
        "placed_order_count": placed_count,
        "canceled_order_count": len(cancellations),
        "open_order_count": len(open_orders) + placed_count,
        "total_capital_at_risk": round(sum(float(row.get("capital_at_risk") or 0.0) for row in placed_rows), 8),
        "max_capital_limit": config.get("max_total_capital_usd"),
        "kill_switch_triggered": False,
        "blocker_counts": [{"reason": key, "count": value} for key, value in sorted(blocker_counts.items())],
        "credentials_present": credentials.get("credentials_present"),
        "paper_runner_unchanged": True,
        "live_order_path": placed_count > 0,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weather LP reward autonomous tiny-live audit runner, default no-op.")
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--selected-quotes", default="evidence/weather_lp_rewards/manual_audit_selected_quotes.json")
    parser.add_argument("--impact-rows", default="evidence/weather_lp_rewards/tiny_live_impact_rows.jsonl")
    parser.add_argument("--kill-switch", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--selected-orders-output", default="evidence/weather_lp_rewards/tiny_live_selected_orders.json")
    parser.add_argument("--selected-orders-csv", default="evidence/weather_lp_rewards/tiny_live_selected_orders.csv")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/tiny_live_runner_report.json")
    parser.add_argument("--orders-output", default="evidence/weather_lp_rewards/tiny_live_orders.jsonl")
    parser.add_argument("--updates-output", default="evidence/weather_lp_rewards/tiny_live_order_updates.jsonl")
    parser.add_argument("--cancellations-output", default="evidence/weather_lp_rewards/tiny_live_cancellations.jsonl")
    parser.add_argument("--errors-output", default="evidence/weather_lp_rewards/tiny_live_errors.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_tiny_live_runner_report(args, env=dict(os.environ))
    write_json(args.summary_output, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
