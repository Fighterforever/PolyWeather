#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.weather_lp_tiny_live_gap import build_weather_lp_tiny_live_readiness_report, load_json, write_json  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Weather LP tiny-live readiness report without enabling live.")
    parser.add_argument("--experiment-report", default="evidence/weather_lp_rewards/weather_lp_experiment_report.json")
    parser.add_argument("--selected-orders-report", default="evidence/weather_lp_rewards/tiny_live_selected_orders.json")
    parser.add_argument("--runner-report", default="evidence/weather_lp_rewards/tiny_live_runner_report.json")
    parser.add_argument("--systemd-install-report", default="evidence/weather_lp_rewards/tiny_live_systemd_install_report.json")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/weather_lp_tiny_live_readiness_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_weather_lp_tiny_live_readiness_report(
        weather_lp_experiment_report=load_json(args.experiment_report),
        tiny_live_selected_orders_report=load_json(args.selected_orders_report),
        tiny_live_runner_report=load_json(args.runner_report),
        tiny_live_systemd_install_report=load_json(args.systemd_install_report),
        env=dict(os.environ),
    )
    write_json(args.summary_output, report)
    print(json.dumps({"recommendation": report.get("recommendation"), "credentials_present": report.get("credentials_present"), "live_order_path_default": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
