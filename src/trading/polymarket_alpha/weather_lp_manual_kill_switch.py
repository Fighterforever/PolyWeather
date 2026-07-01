from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_manual_kill_switch.v1"


def build_weather_lp_manual_kill_switch_checklist(
    *,
    position_sizing_report: Dict[str, Any],
    kill_switch_policy_report: Dict[str, Any],
    operator_confirmed: bool = False,
) -> Dict[str, Any]:
    manual_cancel_steps = [
        "Open Polymarket portfolio/orders page manually.",
        "Filter active Weather LP reward quotes by market slug/token.",
        "Cancel all quotes if any emergency stop condition is triggered.",
        "Record cancellation timestamp and reason in the paper ledger.",
    ]
    configured_total = position_sizing_report.get("recommended_total_capital_at_risk") or position_sizing_report.get("total_weather_lp_exposure_cap_dollars")
    try:
        max_total = min(float(configured_total or 70.0), 70.0)
    except (TypeError, ValueError):
        max_total = 70.0
    try:
        per_market = min(float(position_sizing_report.get("per_market_risk_cap_dollars") or 10.0), 10.0)
    except (TypeError, ValueError):
        per_market = 10.0
    try:
        per_city = min(float(position_sizing_report.get("per_city_exposure_cap_dollars") or 25.0), 25.0)
    except (TypeError, ValueError):
        per_city = 25.0
    try:
        max_daily_loss = min(float(kill_switch_policy_report.get("max_daily_loss_cents") or 1000.0), 1000.0)
    except (TypeError, ValueError):
        max_daily_loss = 1000.0
    max_open_quotes = min(int(kill_switch_policy_report.get("max_open_quotes") or 11), 11)
    missing: List[str] = []
    if not kill_switch_policy_report.get("daily_stop_loss_ready"):
        missing.append("daily_stop_loss")
    required_fields = {
        "max_total_capital_at_risk": max_total,
        "max_per_market_capital": per_market,
        "max_per_city_capital": per_city,
        "max_open_quotes": max_open_quotes,
        "max_daily_loss": max_daily_loss,
        "max_loss_per_quote": per_market,
        "markout_loss_cancel_threshold": "-3c",
        "reward_disqualified_cancel": True,
        "hour_boundary_cancel": True,
        "city_peak_window_cancel": True,
        "max_quote_age": kill_switch_policy_report.get("max_quote_age_minutes") or 180,
        "spread_widening_cancel": True,
        "orderbook_depth_collapse_cancel": True,
        "emergency_stop_condition": "cancel all manual quotes if daily loss, reward disqualification, stale quote, spread widening, or depth collapse trigger fires",
        "manual_operator_required": True,
        "UI-only manual execution reminder": "Human operator must place/cancel orders manually in Polymarket UI; no script may place orders.",
        "do_not_auto_trade": True,
        "live_order_path": False,
    }
    for key, value in required_fields.items():
        if value is None or value == "":
            missing.append(key)
    status = "ready" if not missing else "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "manual_cancel_steps": manual_cancel_steps,
        **required_fields,
        "daily_stop_loss": max_daily_loss,
        "manual_operator_required": True,
        "operator_confirmation_required": True,
        "operator_confirmed": operator_confirmed,
        "ui_only_manual_execution_reminder": required_fields["UI-only manual execution reminder"],
        "checklist_status": status,
        "ready": status == "ready",
        "missing_items": missing,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Weather LP Manual Kill-Switch Checklist",
        "",
        f"Status: {report.get('checklist_status')}",
        "",
        "This checklist is for manual review only. It does not enable live trading.",
        "",
        "## Cancel Steps",
    ]
    for step in report.get("manual_cancel_steps") or []:
        lines.append(f"- {step}")
    lines.extend(
        [
            "",
            "## Limits",
            f"- Max total capital at risk: {report.get('max_total_capital_at_risk')}",
            f"- Max per market capital: {report.get('max_per_market_capital')}",
            f"- Max per city capital: {report.get('max_per_city_capital')}",
            f"- Max open quotes: {report.get('max_open_quotes')}",
            f"- Daily stop loss cents: {report.get('max_daily_loss') or report.get('daily_stop_loss')}",
            f"- Max loss per quote: {report.get('max_loss_per_quote')}",
            f"- Markout loss cancel threshold: {report.get('markout_loss_cancel_threshold')}",
            "",
            "## Required Manual Controls",
            f"- Reward disqualified cancel: {report.get('reward_disqualified_cancel')}",
            f"- Hour boundary cancel: {report.get('hour_boundary_cancel')}",
            f"- City peak window cancel: {report.get('city_peak_window_cancel')}",
            f"- Spread widening cancel: {report.get('spread_widening_cancel')}",
            f"- Orderbook depth collapse cancel: {report.get('orderbook_depth_collapse_cancel')}",
            f"- UI-only reminder: {report.get('ui_only_manual_execution_reminder')}",
            "",
            "## Emergency Stop",
            f"- {report.get('emergency_stop_condition')}",
            "",
            f"do_not_auto_trade={str(report.get('do_not_auto_trade')).lower()}",
            f"live_order_path={str(report.get('live_order_path')).lower()}",
        ]
    )
    return "\n".join(lines) + "\n"


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


__all__ = ["SCHEMA_VERSION", "build_weather_lp_manual_kill_switch_checklist", "load_json", "render_markdown", "write_json", "write_text"]
