from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_kill_switch_policy.v1"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_weather_lp_kill_switch_policy_report(
    *,
    reward_risk_report: Dict[str, Any],
    profitability_simulation_report: Dict[str, Any],
    position_sizing_report: Dict[str, Any] | None = None,
    max_daily_loss_cents: float = 300.0,
    max_quote_age_minutes: int = 180,
    max_open_quotes: int = 30,
) -> Dict[str, Any]:
    position_sizing_report = position_sizing_report or {}
    stress = profitability_simulation_report.get("stress_table") or {}
    scenario = profitability_simulation_report.get("scenario_table") or {}
    controls = {
        "cancel_at_hour_boundary": True,
        "cancel_on_reward_disqualified": True,
        "cancel_on_markout_loss_threshold": True,
        "cancel_on_city_peak_window": False,
        "cancel_on_spread_widening": True,
        "cancel_on_orderbook_depth_collapse": True,
        "daily_stop_loss": True,
        "max_quote_age": True,
        "max_open_quotes": True,
        "max_city_exposure": bool(position_sizing_report.get("city_exposure_cap_ready", True)),
        "max_total_capital_at_risk": bool(position_sizing_report.get("max_total_capital_at_risk_ready", True)),
        "manual_kill_switch_required": True,
        "manual_kill_switch_ready": False,
    }
    missing_controls = [key for key, ready in controls.items() if key.endswith("_ready") and not ready]
    if not controls["cancel_on_city_peak_window"]:
        missing_controls.append("cancel_on_city_peak_window")
    current_markout = _safe_float(reward_risk_report.get("mean_current_markout"))
    recommended_action = "continue_paper_with_controls"
    if current_markout is not None and current_markout < 0:
        recommended_action = "tighten_cancellation_after_negative_markout"
    if _safe_float(stress.get("minus_3c")) is not None and float(stress.get("minus_3c")) < 0:
        recommended_action = "tighten_cancellation_and_sizing_for_minus_3c_stress"
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "policy_fields": controls,
        "controls_ready": {
            "paper_controls_ready": True,
            "future_live_controls_ready": False,
            "daily_stop_loss_ready": controls["daily_stop_loss"],
            "manual_kill_switch_ready": controls["manual_kill_switch_ready"],
        },
        "missing_controls": sorted(set(missing_controls)),
        "daily_stop_loss_ready": True,
        "manual_kill_switch_ready": False,
        "cancellation_policy_ready": True,
        "max_daily_loss_cents": max_daily_loss_cents,
        "max_quote_age_minutes": max_quote_age_minutes,
        "max_open_quotes": max_open_quotes,
        "stress_minus_3c_net_cents": stress.get("minus_3c"),
        "base_scenario_net_cents": scenario.get("base_net"),
        "recommended_action": recommended_action,
    }


def load_json(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["SCHEMA_VERSION", "build_weather_lp_kill_switch_policy_report", "load_json", "write_json"]
