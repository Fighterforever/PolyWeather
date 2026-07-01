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
    max_total = position_sizing_report.get("recommended_total_capital_at_risk") or position_sizing_report.get("total_weather_lp_exposure_cap_dollars")
    missing: List[str] = []
    if not operator_confirmed:
        missing.append("operator_confirmation_required")
    if not kill_switch_policy_report.get("daily_stop_loss_ready"):
        missing.append("daily_stop_loss")
    status = "ready" if not missing else "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "manual_cancel_steps": manual_cancel_steps,
        "max_total_capital_at_risk": max_total,
        "max_per_market_capital": position_sizing_report.get("per_market_risk_cap_dollars"),
        "max_per_city_capital": position_sizing_report.get("per_city_exposure_cap_dollars"),
        "max_open_quotes": kill_switch_policy_report.get("max_open_quotes"),
        "daily_stop_loss": kill_switch_policy_report.get("max_daily_loss_cents"),
        "markout_loss_cancel_threshold": "-1c to -3c paper stress review before any manual action",
        "reward_disqualified_cancel": True,
        "hour_boundary_cancel": True,
        "city_peak_window_cancel": "manual weather peak-window review required",
        "emergency_stop_condition": "cancel all manual quotes if cumulative adverse markout exceeds daily stop loss or reward qualification disappears",
        "operator_confirmation_required": True,
        "operator_confirmed": operator_confirmed,
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
            f"- Daily stop loss cents: {report.get('daily_stop_loss')}",
            "",
            "## Emergency Stop",
            f"- {report.get('emergency_stop_condition')}",
            "",
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
