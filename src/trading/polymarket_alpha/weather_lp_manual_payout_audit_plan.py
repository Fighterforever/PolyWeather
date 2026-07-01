from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.trading.polymarket_alpha.probability_dataset import write_json


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_manual_payout_audit_plan.v1"


def build_manual_payout_audit_plan(
    *,
    selected_quotes_report: Dict[str, Any],
    manual_kill_switch_checklist: Dict[str, Any],
    reward_payout_audit_report: Dict[str, Any],
    recommended_first_test_capital: float = 25.0,
    max_total_capital_at_risk: float = 70.0,
) -> Dict[str, Any]:
    selected_count = min(int(selected_quotes_report.get("selected_quote_count") or 0), 3)
    selected_capital = float(selected_quotes_report.get("total_selected_capital_at_risk") or 0.0)
    first_capital = min(float(recommended_first_test_capital), 25.0, selected_capital or 25.0)
    max_capital = min(float(max_total_capital_at_risk), 70.0)
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_objective": [
            "verify actual liquidity reward payout",
            "compare paper expected reward scenarios with actual received rewards",
            "measure real markout/adverse selection",
        ],
        "explicit_constraints": {
            "manual_execution_only": True,
            "no_auto_trade": True,
            "live_order_path": False,
            "user_must_place_and_cancel_manually": True,
            "manual_ui_only": True,
            "no_api_order_instructions": True,
        },
        "maximum_capital": {
            "max_total_capital_at_risk": max_capital,
            "recommended_first_test_capital": first_capital,
            "selected_quote_capital_at_risk": selected_capital,
        },
        "max_number_of_manual_quotes": {
            "first_test_quote_count": selected_count,
            "absolute_max_first_test_quote_count": 3,
            "selected_from_existing_manual_order_sheet": True,
        },
        "test_duration_options": [
            "30 minutes",
            "60 minutes",
            "until reward disqualified",
        ],
        "cancel_rules": {
            "cancel_on_reward_disqualified": True,
            "cancel_on_markout_loss_threshold": manual_kill_switch_checklist.get("markout_loss_cancel_threshold") or "-3c",
            "cancel_at_hour_boundary": True,
            "cancel_on_spread_widening": True,
            "manual_emergency_cancel": True,
            "manual_operator_required": True,
        },
        "expected_artifacts_after_manual_test": [
            "manual_payout_audit_filled.csv",
            "actual_reward_received",
            "actual_markout",
            "cancellation_time",
            "discrepancy_reason",
        ],
        "selected_quote_count": selected_count,
        "selected_quote_report_path": "evidence/weather_lp_rewards/manual_audit_selected_quotes.json",
        "reward_payout_audit_status": reward_payout_audit_report.get("audit_status"),
        "plan_status": "ready_for_user_manual_audit" if selected_count > 0 else "blocked_no_selected_quotes",
        "manual_execution_only": True,
        "manual_review_required": True,
        "do_not_auto_trade": True,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Weather LP Manual Tiny-Live Payout Audit Plan",
        "",
        "This is a manual UI-only audit plan. It does not contain executable order payloads or API order instructions.",
        "",
        "## Objectives",
    ]
    for item in report.get("audit_objective") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Constraints",
            "- manual_execution_only=true",
            "- no_auto_trade=true",
            "- user_must_place_and_cancel_manually=true",
            "- live_order_path=false",
            "",
            "## Capital And Scope",
            f"- Max total capital at risk: {report.get('maximum_capital', {}).get('max_total_capital_at_risk')} USD",
            f"- Recommended first test capital: {report.get('maximum_capital', {}).get('recommended_first_test_capital')} USD",
            f"- First test quote count: {report.get('max_number_of_manual_quotes', {}).get('first_test_quote_count')}",
            "",
            "## Cancel Rules",
        ]
    )
    for key, value in (report.get("cancel_rules") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Expected Manual Artifacts After Test",
        ]
    )
    for item in report.get("expected_artifacts_after_manual_test") or []:
        lines.append(f"- {item}")
    lines.extend(["", "manual UI only", "live_order_path=false"])
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


__all__ = ["SCHEMA_VERSION", "build_manual_payout_audit_plan", "load_json", "render_markdown", "write_json", "write_text"]
