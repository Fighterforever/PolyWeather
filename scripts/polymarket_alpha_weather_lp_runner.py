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
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), text=True, capture_output=True, check=False)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}


def _load(path: str | Path) -> Dict[str, Any]:
    source = PROJECT_ROOT / path
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


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
        [args.python_path, "scripts/polymarket_alpha_weather_lp_strategy_report.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_paper_cycle.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_quote_update_report.py", "--generated-at", generated_at],
        [args.python_path, "scripts/polymarket_alpha_reward_allocation_audit_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_risk_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_reward_window_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_cancellation_report.py"],
        [args.python_path, "scripts/polymarket_alpha_weather_lp_experiment_controller.py"],
        [args.python_path, "scripts/polymarket_alpha_tournament_report.py"],
    ]
    results = [_run(command) for command in commands]
    experiment = _load("evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    discovery = _load("evidence/weather_lp_rewards/lp_reward_discovery_report.json")
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
        "reward_points_proxy": experiment.get("reward_points_proxy"),
        "estimated_reward_cents_proxy": experiment.get("estimated_reward_cents_proxy"),
        "reward_to_risk_proxy": experiment.get("reward_to_risk_proxy"),
        "recommendation": experiment.get("recommendation"),
        "commands": results,
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
