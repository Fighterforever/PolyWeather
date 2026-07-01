#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(command: List[str]) -> Dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=str(PROJECT_ROOT), text=True, capture_output=True, check=False)
    except OSError as exc:
        return {"command": command, "returncode": 127, "stdout": "", "stderr": type(exc).__name__}
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}


def _load(path: str | Path) -> Dict[str, Any]:
    source = PROJECT_ROOT / path
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def _write_safety_status(*, generated_at: str) -> Dict[str, Any]:
    timers = _run(["systemctl", "list-timers", "--all", "--no-pager"])
    units = _run(["systemctl", "list-units", "--all", "--no-pager"])
    unit_files = _run(["systemctl", "list-unit-files", "--no-pager"])
    ports = _run(["ss", "-tulpn"])
    disk = _run(["df", "-h", "/", "/opt"])
    memory = _run(["free", "-m"])
    text = "\n".join([timers.get("stdout", ""), units.get("stdout", "")]).lower()
    unit_file_lines = [
        line.strip()
        for line in str(unit_files.get("stdout") or "").splitlines()
        if any(key in line.lower() for key in ("polyweather", "polymarket", "weather"))
    ]
    disabled_timers = [line.split()[0] for line in unit_file_lines if line.split() and line.split()[0].endswith(".timer") and "disabled" in line]
    disabled_services = [line.split()[0] for line in unit_file_lines if line.split() and line.split()[0].endswith(".service") and "disabled" in line]
    report = {
        "schema_version": "polyweather_vps_safety_status.v1",
        "generated_at": generated_at,
        "kept_timers": ["polyweather-weather-lp-reward.timer"] if "polyweather-weather-lp-reward.timer" in text else [],
        "disabled_timers": disabled_timers,
        "disabled_services": disabled_services,
        "active_ports": ports,
        "disk_usage": disk,
        "memory_usage": memory,
        "timer_status_raw": timers,
        "unit_status_raw": units,
        "unit_file_status_raw": unit_files,
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    output = PROJECT_ROOT / "evidence/vps_runtime/vps_safety_status_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paper-only weather LP reward experiment chain.")
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/vps_weather_lp_runner_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    commands = [
        [args.python_path, "scripts/polymarket_alpha_ingest_external_strategy.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_discovery_report.py", "--generated-at", generated_at, "--no-record-window-observation"],
        [args.python_path, "scripts/polymarket_alpha_reward_metadata_audit_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_discovery_report.py", "--generated-at", generated_at, "--record-window-observation"],
        [args.python_path, "scripts/polymarket_alpha_weather_city_regime_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_smart_holder_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_quote_optimizer_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_strategy_report.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_paper_cycle.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_quote_update_report.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_quote_lifecycle_audit_report.py"],
        [args.python_path, "scripts/polymarket_alpha_reward_allocation_audit_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_measurement_cohort_report.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_share_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_risk_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_dollarization_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_profitability_simulation_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_position_sizing_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_kill_switch_policy_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_payout_audit_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_manual_kill_switch_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_manual_order_sheet_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_live_impact_simulator_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_tiny_live_gap_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_profitability_dashboard.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_window_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_cancellation_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_experiment_controller.py"],
        [args.python_path, "scripts/polymarket_alpha_tournament_report.py"],
    ]
    results = [_run(command) for command in commands]
    experiment = _load("evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    discovery = _load("evidence/weather_lp_rewards/lp_reward_discovery_report.json")
    optimizer = _load("evidence/weather_lp_rewards/quote_optimizer_report.json")
    reward_risk = _load("evidence/weather_lp_rewards/reward_vs_risk_report.json")
    lifecycle = _load("evidence/weather_lp_rewards/quote_lifecycle_audit_report.json")
    cohort = _load("evidence/weather_lp_rewards/measurement_cohort_report.json")
    reward_share = _load("evidence/weather_lp_rewards/reward_share_estimator_report.json")
    allocation = _load("evidence/weather_lp_rewards/reward_allocation_audit_report.json")
    dollarization = _load("evidence/weather_lp_rewards/reward_dollarization_report.json")
    dashboard = _load("evidence/weather_lp_rewards/weather_lp_profitability_dashboard.json")
    profitability_simulation = _load("evidence/weather_lp_rewards/profitability_simulation_report.json")
    tiny_live_gap = _load("evidence/weather_lp_rewards/weather_lp_tiny_live_gap_report.json")
    position_sizing = _load("evidence/weather_lp_rewards/position_sizing_report.json")
    kill_switch = _load("evidence/weather_lp_rewards/kill_switch_policy_report.json")
    payout_audit = _load("evidence/weather_lp_rewards/reward_payout_audit_report.json")
    manual_sheet = _load("evidence/weather_lp_rewards/manual_tiny_live_order_sheet_report.json")
    impact_sim = _load("evidence/weather_lp_rewards/tiny_live_impact_simulator_report.json")
    manual_kill = _load("evidence/weather_lp_rewards/manual_kill_switch_checklist.json")
    safety = _write_safety_status(generated_at=generated_at)
    report = {
        "schema_version": "polyweather_polymarket_alpha_weather_lp_runner.v1",
        "generated_at": generated_at,
        "success": all(row["returncode"] == 0 for row in results),
        "reward_market_count": discovery.get("reward_market_count", 0),
        "reward_metadata_available_count": discovery.get("reward_metadata_available_count", 0),
        "min_incentive_size_found_count": discovery.get("min_incentive_size_found_count", 0),
        "max_incentive_spread_found_count": discovery.get("max_incentive_spread_found_count", 0),
        "paper_quote_count": experiment.get("paper_quote_count", 0),
        "quote_update_count": experiment.get("quote_update_count", 0),
        "unique_quote_id_count": experiment.get("unique_quote_id_count") or lifecycle.get("unique_quote_id_count"),
        "updates_per_quote_median": experiment.get("updates_per_quote_median") or lifecycle.get("updates_per_quote_median"),
        "active_cohort_count": experiment.get("active_cohort_count") or cohort.get("active_cohort_count"),
        "cohort_created_count": experiment.get("cohort_created_count") or cohort.get("cohort_created_count") or cohort.get("cohorts_created_count"),
        "lifecycle_audit_conclusion": experiment.get("lifecycle_audit_conclusion") or lifecycle.get("conclusion"),
        "reward_points_proxy": experiment.get("reward_points_proxy"),
        "cumulative_reward_points_proxy": experiment.get("cumulative_reward_points_proxy"),
        "estimated_reward_cents_proxy": experiment.get("estimated_reward_cents_proxy"),
        "mean_markout_5m": experiment.get("mean_5m_markout"),
        "mean_markout_15m": experiment.get("mean_15m_markout"),
        "mean_markout_1h": experiment.get("mean_1h_markout"),
        "mean_markout_6h": experiment.get("mean_6h_markout"),
        "mean_markout_24h": experiment.get("mean_24h_markout"),
        "mean_markout_current": experiment.get("mean_current_markout"),
        "reward_to_risk_proxy": experiment.get("reward_to_risk_proxy"),
        "visible_reward_share_median": experiment.get("visible_reward_share_median") or reward_share.get("visible_reward_share_median"),
        "break_even_share_median": experiment.get("break_even_share_median") or reward_risk.get("break_even_share_median"),
        "break_even_share_minus_1c_median": experiment.get("break_even_share_minus_1c_median") or reward_share.get("median_break_even_share_under_minus_1c"),
        "break_even_share_minus_3c_median": experiment.get("break_even_share_minus_3c_median") or reward_share.get("median_break_even_share_under_minus_3c"),
        "allocation_exact_available": experiment.get("allocation_exact_available") or bool(allocation.get("estimated_reward_cents_available_count")),
        "exact_reward_available": experiment.get("exact_reward_available") or bool(dollarization.get("exact_reward_cents_available_count")),
        "scenario_reward_daily_allocation_10": experiment.get("scenario_reward_daily_allocation_10") or dollarization.get("scenario_total_reward_if_daily_allocation_10"),
        "scenario_reward_visible_median_share": experiment.get("scenario_reward_visible_median_share"),
        "observed_markout_total_cents": experiment.get("observed_markout_total_cents") or dollarization.get("observed_markout_total_cents"),
        "break_even_daily_allocation_for_minus_3c": experiment.get("break_even_daily_allocation_for_minus_3c") or dollarization.get("break_even_daily_allocation_median"),
        "profitability_dashboard_recommendation": dashboard.get("recommendation"),
        "profitability_simulation_scenario_table": profitability_simulation.get("scenario_table"),
        "profitability_simulation_roi_table": profitability_simulation.get("roi_table"),
        "profitability_simulation_stress_table": profitability_simulation.get("stress_table"),
        "total_capital_locked_proxy": profitability_simulation.get("total_capital_locked_proxy"),
        "tiny_live_gap_summary": {
            "current_status": tiny_live_gap.get("current_status"),
            "tiny_live_not_allowed_reason": tiny_live_gap.get("tiny_live_not_allowed_reason"),
            "missing_controls": tiny_live_gap.get("missing_controls"),
            "final_status": tiny_live_gap.get("final_status"),
        },
        "position_sizing_summary": {
            "recommended_total_capital_at_risk": position_sizing.get("recommended_total_capital_at_risk"),
            "recommended_quote_count": position_sizing.get("recommended_quote_count"),
            "rejected_quote_count": position_sizing.get("rejected_quote_count"),
        },
        "kill_switch_summary": {
            "recommended_action": kill_switch.get("recommended_action"),
            "missing_controls": kill_switch.get("missing_controls"),
        },
        "reward_payout_audit_status": payout_audit.get("audit_status"),
        "manual_quote_count": manual_sheet.get("suggested_manual_quote_count"),
        "total_manual_quote_capital_at_risk": manual_sheet.get("total_capital_at_risk_if_all_manual_quotes_used"),
        "impact_simulation_ready": impact_sim.get("impact_simulation_ready"),
        "kill_switch_ready": manual_kill.get("ready"),
        "next_expected_5m_markout_time": experiment.get("next_expected_5m_markout_time") or cohort.get("next_expected_5m_markout_time"),
        "next_expected_15m_markout_time": experiment.get("next_expected_15m_markout_time") or cohort.get("next_expected_15m_markout_time"),
        "next_expected_1h_markout_time": experiment.get("next_expected_1h_markout_time") or cohort.get("next_expected_1h_markout_time"),
        "experiment_recommendation": experiment.get("recommendation"),
        "recommendation": experiment.get("recommendation"),
        "quote_optimizer_selected_count": optimizer.get("selected_quote_count"),
        "rejected_expensive_basket_count": optimizer.get("rejected_expensive_basket_count"),
        "valid_markout_count_by_horizon": reward_risk.get("valid_markout_count_by_horizon"),
        "disk_usage": _run(["df", "-h", "."]),
        "vps_safety_status": {
            "kept_timers": safety.get("kept_timers"),
            "disabled_timers": safety.get("disabled_timers"),
            "live_order_path": False,
        },
        "commands": results,
        "artifact_paths": {
            "reward_metadata_audit": "evidence/weather_lp_rewards/reward_metadata_audit_report.json",
            "quote_optimizer": "evidence/weather_lp_rewards/quote_optimizer_report.json",
            "paper_quotes": "evidence/weather_lp_rewards/paper_quotes.jsonl",
            "paper_quote_updates": "evidence/weather_lp_rewards/paper_quote_updates.jsonl",
            "quote_lifecycle_audit": "evidence/weather_lp_rewards/quote_lifecycle_audit_report.json",
            "measurement_cohorts": "evidence/weather_lp_rewards/measurement_cohorts.jsonl",
            "measurement_cohort_report": "evidence/weather_lp_rewards/measurement_cohort_report.json",
            "reward_share_estimator": "evidence/weather_lp_rewards/reward_share_estimator_report.json",
            "reward_vs_risk": "evidence/weather_lp_rewards/reward_vs_risk_report.json",
            "reward_dollarization": "evidence/weather_lp_rewards/reward_dollarization_report.json",
            "profitability_simulation": "evidence/weather_lp_rewards/profitability_simulation_report.json",
            "tiny_live_gap": "evidence/weather_lp_rewards/weather_lp_tiny_live_gap_report.json",
            "position_sizing": "evidence/weather_lp_rewards/position_sizing_report.json",
            "kill_switch_policy": "evidence/weather_lp_rewards/kill_switch_policy_report.json",
            "reward_payout_audit": "evidence/weather_lp_rewards/reward_payout_audit_report.json",
            "manual_order_sheet": "evidence/weather_lp_rewards/manual_tiny_live_order_sheet_report.json",
            "impact_simulator": "evidence/weather_lp_rewards/tiny_live_impact_simulator_report.json",
            "manual_kill_switch": "evidence/weather_lp_rewards/manual_kill_switch_checklist.json",
            "profitability_dashboard": "evidence/weather_lp_rewards/weather_lp_profitability_dashboard.json",
            "reward_allocation_audit": "evidence/weather_lp_rewards/reward_allocation_audit_report.json",
            "reward_window": "evidence/weather_lp_rewards/reward_window_report.json",
            "cancellation_policy": "evidence/weather_lp_rewards/cancellation_policy_report.json",
            "experiment": "evidence/weather_lp_rewards/weather_lp_experiment_report.json",
            "alpha_tournament": "evidence/polymarket_alpha/alpha_tournament_scoreboard.json",
            "vps_safety_status": "evidence/vps_runtime/vps_safety_status_report.json",
        },
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
    }
    output = PROJECT_ROOT / args.summary_output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"success": report["success"], "reward_market_count": report["reward_market_count"], "paper_quote_count": report["paper_quote_count"], "live_order_path": False}, indent=2, sort_keys=True))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
