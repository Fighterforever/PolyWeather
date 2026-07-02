#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_alpha.probability_dataset import write_json  # noqa: E402


ENV_TEMPLATE = """# Weather LP tiny-live audit env template.
# Default values are safe no-op. Do not commit a filled copy with secrets.
POLYWEATHER_ENABLE_LIVE=false
POLYWEATHER_ENABLE_WEATHER_LP_TINY_LIVE=false
POLYWEATHER_LIVE_STRATEGY_ALLOWLIST=weather_lp_reward
POLYWEATHER_MAX_TOTAL_CAPITAL_USD=25
POLYWEATHER_MAX_PER_MARKET_USD=5
POLYWEATHER_MAX_PER_CITY_USD=10
POLYWEATHER_MAX_OPEN_ORDERS=3
POLYWEATHER_DAILY_STOP_LOSS_USD=10
POLYWEATHER_REQUIRE_RESTING_ONLY=true
POLYWEATHER_ALLOW_TAKER=false
POLYWEATHER_ORDER_TYPE_ALLOWLIST=GTD

# Polymarket credentials. Fill only on the VPS env file if the user explicitly
# chooses to run the tiny-live audit.
POLYMARKET_PRIVATE_KEY=
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_PASSPHRASE=
POLYMARKET_FUNDER_ADDRESS=
POLYMARKET_SIGNATURE_TYPE=
"""


SERVICE_TEMPLATE = """[Unit]
Description=PolyWeather Weather LP tiny-live audit runner
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={workdir}
EnvironmentFile=-/etc/polyweather/weather_lp_tiny_live.env
ExecStart={python_path} scripts/polymarket_alpha_weather_lp_tiny_live_runner.py
User=root
"""


TIMER_TEMPLATE = """[Unit]
Description=PolyWeather Weather LP tiny-live audit runner timer (disabled by default)

[Timer]
OnCalendar=*:0/5
Persistent=false
Unit=polyweather-weather-lp-tiny-live.service

[Install]
WantedBy=timers.target
"""


def _run(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        return {"command": command, "returncode": 127, "stdout": "", "stderr": type(exc).__name__}
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}


def install_systemd_units(*, python_path: str, dry_run: bool = False) -> dict:
    env_template_path = PROJECT_ROOT / "evidence/weather_lp_rewards/weather_lp_tiny_live_env_template.env"
    env_template_path.parent.mkdir(parents=True, exist_ok=True)
    env_template_path.write_text(ENV_TEMPLATE, encoding="utf-8")
    service_path = Path("/etc/systemd/system/polyweather-weather-lp-tiny-live.service")
    timer_path = Path("/etc/systemd/system/polyweather-weather-lp-tiny-live.timer")
    commands = []
    service_written = False
    timer_written = False
    if not dry_run:
        service_path.write_text(SERVICE_TEMPLATE.format(workdir=PROJECT_ROOT, python_path=python_path), encoding="utf-8")
        timer_path.write_text(TIMER_TEMPLATE, encoding="utf-8")
        service_written = True
        timer_written = True
        commands.append(_run(["systemctl", "daemon-reload"]))
        commands.append(_run(["systemctl", "disable", "--now", "polyweather-weather-lp-tiny-live.timer"]))
        commands.append(_run(["systemctl", "disable", "polyweather-weather-lp-tiny-live.service"]))
    status = _run(["systemctl", "is-enabled", "polyweather-weather-lp-tiny-live.timer"]) if not dry_run else {"stdout": "disabled", "returncode": 1}
    report = {
        "schema_version": "polyweather_polymarket_alpha_weather_lp_tiny_live_systemd_install.v1",
        "service_installed": service_written,
        "timer_installed": timer_written,
        "timer_enabled": str(status.get("stdout") or "").strip() == "enabled",
        "env_file_required": True,
        "env_file_path": "/etc/polyweather/weather_lp_tiny_live.env",
        "env_template_path": str(env_template_path),
        "commands": commands,
        "dry_run": dry_run,
        "live_order_path_default": False,
        "live_order_path": False,
    }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install optional Weather LP tiny-live systemd units disabled by default.")
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary-output", default="evidence/weather_lp_rewards/tiny_live_systemd_install_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = install_systemd_units(python_path=args.python_path, dry_run=args.dry_run)
    write_json(args.summary_output, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
