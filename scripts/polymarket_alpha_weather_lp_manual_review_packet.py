#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_dataset import write_json  # noqa: E402


SCHEMA_VERSION = "polyweather_polymarket_alpha_weather_lp_manual_review_packet.v1"


def _load(path: str | Path) -> Dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _bool(value: Any) -> bool:
    return bool(value)


def _remaining_blockers(
    *,
    exact_reward_available: bool,
    payout_status: str | None,
    kill_switch_ready: bool,
    impact_ready: bool,
    manual_quote_count: int,
    manual_capital_at_risk: float | None,
    max_total_capital_at_risk: float | None,
) -> List[str]:
    blockers: List[str] = []
    if not exact_reward_available:
        blockers.append("exact_reward_payout_not_verified")
    if payout_status != "payout_observed":
        blockers.append("reward_payout_manual_audit_required")
    if not kill_switch_ready:
        blockers.append("manual_kill_switch_not_ready")
    if not impact_ready:
        blockers.append("live_impact_not_simulated")
    if manual_quote_count <= 0:
        blockers.append("manual_order_sheet_empty")
    if (
        manual_capital_at_risk is not None
        and max_total_capital_at_risk is not None
        and manual_capital_at_risk > max_total_capital_at_risk
    ):
        blockers.append("manual_quote_capital_exceeds_kill_switch_limit")
    return list(dict.fromkeys(blockers))


def _suggested_decision(
    *,
    kill_switch_ready: bool,
    exact_reward_available: bool,
    manual_quote_count: int,
    impact_ready: bool,
) -> str:
    if not kill_switch_ready:
        return "do_not_live_kill_switch_missing"
    if manual_quote_count <= 0:
        return "continue_paper_no_manual_quotes"
    if not impact_ready:
        return "continue_paper_impact_simulation_required"
    if not exact_reward_available:
        return "prepare_manual_tiny_live_review_but_reward_payout_audit_required"
    return "prepare_manual_tiny_live_review_but_live_disabled"


def build_manual_review_packet(
    *,
    manual_order_sheet_report: Dict[str, Any],
    impact_simulator_report: Dict[str, Any],
    profitability_simulation_report: Dict[str, Any],
    reward_risk_report: Dict[str, Any],
    reward_share_report: Dict[str, Any],
    reward_payout_audit_report: Dict[str, Any],
    manual_kill_switch_checklist: Dict[str, Any],
    tiny_live_gap_report: Dict[str, Any],
    manual_order_sheet_path: str,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    scenario = profitability_simulation_report.get("scenario_table") or {}
    manual_quote_count = int(manual_order_sheet_report.get("suggested_manual_quote_count") or 0)
    impact_ready = _bool(impact_simulator_report.get("impact_simulation_ready"))
    kill_switch_ready = _bool(manual_kill_switch_checklist.get("ready") or manual_kill_switch_checklist.get("checklist_status") == "ready")
    exact_reward_available = _bool(
        profitability_simulation_report.get("exact_reward_available")
        or reward_payout_audit_report.get("exact_reward_available")
        or tiny_live_gap_report.get("exact_reward_available")
    )
    try:
        manual_capital = float(manual_order_sheet_report.get("total_capital_at_risk_if_all_manual_quotes_used"))
    except (TypeError, ValueError):
        manual_capital = None
    try:
        max_total_capital = float(manual_kill_switch_checklist.get("max_total_capital_at_risk"))
    except (TypeError, ValueError):
        max_total_capital = None
    payout_status = reward_payout_audit_report.get("audit_status")
    suggested = _suggested_decision(
        kill_switch_ready=kill_switch_ready,
        exact_reward_available=exact_reward_available,
        manual_quote_count=manual_quote_count,
        impact_ready=impact_ready,
    )
    blockers = _remaining_blockers(
        exact_reward_available=exact_reward_available,
        payout_status=payout_status,
        kill_switch_ready=kill_switch_ready,
        impact_ready=impact_ready,
        manual_quote_count=manual_quote_count,
        manual_capital_at_risk=manual_capital,
        max_total_capital_at_risk=max_total_capital,
    )
    if "manual_quote_capital_exceeds_kill_switch_limit" in blockers:
        suggested = "do_not_live_manual_quote_capital_exceeds_kill_switch_limit"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "manual_review_packet_ready": True,
        "manual_quote_count": manual_quote_count,
        "total_manual_quote_capital_at_risk": manual_order_sheet_report.get("total_capital_at_risk_if_all_manual_quotes_used"),
        "max_total_capital_at_risk": manual_kill_switch_checklist.get("max_total_capital_at_risk"),
        "quote_list_path": manual_order_sheet_path,
        "impact_simulation_status": "ready" if impact_ready else "missing",
        "impact_simulation_ready": impact_ready,
        "base_scenario_net_cents": manual_order_sheet_report.get("base_scenario_net_cents", scenario.get("base_net")),
        "conservative_scenario_net_cents": manual_order_sheet_report.get("conservative_scenario_net_cents", scenario.get("conservative_net")),
        "observed_markout": {
            "total_cents": profitability_simulation_report.get("observed_markout_total_cents"),
            "mean_5m": reward_risk_report.get("mean_5m_markout"),
            "mean_15m": reward_risk_report.get("mean_15m_markout"),
            "mean_1h": reward_risk_report.get("mean_1h_markout"),
            "mean_current": reward_risk_report.get("mean_current_markout"),
            "valid_markout_count_by_horizon": reward_risk_report.get("valid_markout_count_by_horizon"),
        },
        "visible_reward_share_median": reward_share_report.get("visible_reward_share_median")
        or reward_share_report.get("median_our_visible_share_proxy")
        or profitability_simulation_report.get("visible_reward_share_median"),
        "exact_reward_available": exact_reward_available,
        "reward_payout_audit_status": payout_status,
        "reward_payout_audit_ready": payout_status in {"manual_audit_required", "payout_observed"},
        "kill_switch_ready": kill_switch_ready,
        "remaining_blockers": blockers,
        "suggested_human_decision": suggested,
        "explicit_warnings": [
            "no automatic trading",
            "human must place and cancel manually if this is ever reviewed",
            "manual order sheet is not live-ready evidence",
            "scenario reward is not exact payout",
            "live_order_path=false",
        ],
        "manual_review_required": True,
        "do_not_auto_trade": True,
        "live_ready": False,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }


def render_markdown(packet: Dict[str, Any]) -> str:
    lines = [
        "# Weather LP Manual Tiny-Live Review Packet",
        "",
        "This packet is for human review only. It does not create, route, or approve any live order.",
        "",
        f"- Suggested human decision: {packet.get('suggested_human_decision')}",
        f"- Manual quote count: {packet.get('manual_quote_count')}",
        f"- Total manual quote capital at risk: {packet.get('total_manual_quote_capital_at_risk')}",
        f"- Base scenario net cents: {packet.get('base_scenario_net_cents')}",
        f"- Conservative scenario net cents: {packet.get('conservative_scenario_net_cents')}",
        f"- Visible reward share median: {packet.get('visible_reward_share_median')}",
        f"- Exact reward available: {packet.get('exact_reward_available')}",
        f"- Reward payout audit status: {packet.get('reward_payout_audit_status')}",
        f"- Kill switch ready: {packet.get('kill_switch_ready')}",
        f"- Impact simulation ready: {packet.get('impact_simulation_ready')}",
        "",
        "## Remaining Blockers",
    ]
    blockers = packet.get("remaining_blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("- none, but live order path remains disabled")
    lines.extend(
        [
            "",
            "## Explicit Warnings",
        ]
    )
    for warning in packet.get("explicit_warnings") or []:
        lines.append(f"- {warning}")
    lines.extend(
        [
            "",
            "manual_review_required=true",
            "do_not_auto_trade=true",
            "live_order_path=false",
        ]
    )
    return "\n".join(lines) + "\n"


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Weather LP manual tiny-live review packet without order execution.")
    parser.add_argument("--manual-order-sheet-report", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet_report.json")
    parser.add_argument("--manual-order-sheet-path", default="evidence/weather_lp_rewards/manual_tiny_live_order_sheet.csv")
    parser.add_argument("--impact-simulator-report", default="evidence/weather_lp_rewards/tiny_live_impact_simulator_report.json")
    parser.add_argument("--profitability-simulation-report", default="evidence/weather_lp_rewards/profitability_simulation_report.json")
    parser.add_argument("--reward-risk-report", default="evidence/weather_lp_rewards/reward_vs_risk_report.json")
    parser.add_argument("--reward-share-report", default="evidence/weather_lp_rewards/reward_share_estimator_report.json")
    parser.add_argument("--reward-payout-audit-report", default="evidence/weather_lp_rewards/reward_payout_audit_report.json")
    parser.add_argument("--manual-kill-switch-checklist", default="evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    parser.add_argument("--tiny-live-gap-report", default="evidence/weather_lp_rewards/weather_lp_tiny_live_gap_report.json")
    parser.add_argument("--json-output", default="evidence/weather_lp_rewards/manual_tiny_live_review_packet.json")
    parser.add_argument("--markdown-output", default="evidence/weather_lp_rewards/manual_tiny_live_review_packet.md")
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    packet = build_manual_review_packet(
        manual_order_sheet_report=_load(args.manual_order_sheet_report),
        impact_simulator_report=_load(args.impact_simulator_report),
        profitability_simulation_report=_load(args.profitability_simulation_report),
        reward_risk_report=_load(args.reward_risk_report),
        reward_share_report=_load(args.reward_share_report),
        reward_payout_audit_report=_load(args.reward_payout_audit_report),
        manual_kill_switch_checklist=_load(args.manual_kill_switch_checklist),
        tiny_live_gap_report=_load(args.tiny_live_gap_report),
        manual_order_sheet_path=args.manual_order_sheet_path,
        generated_at=args.generated_at,
    )
    write_json(args.json_output, packet)
    write_text(args.markdown_output, render_markdown(packet))
    print(
        json.dumps(
            {
                "manual_review_packet_ready": packet.get("manual_review_packet_ready"),
                "suggested_human_decision": packet.get("suggested_human_decision"),
                "live_order_path": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
