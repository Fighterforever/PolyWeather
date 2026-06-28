#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABEL = "com.polyweather.eq-dead-no-sampler"


def _default_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return shutil.which("python3") or sys.executable


def build_program_arguments(args: argparse.Namespace) -> list[str]:
    repo_root = Path(args.repo_root).resolve()
    program = [
        str(args.python_path or _default_python(repo_root)),
        str(repo_root / "scripts" / "weather_eq_dead_no_execution_sampler.py"),
        "--paper-only",
        "--collect-intraday-before-scan",
        "--intraday-observation-path",
        args.intraday_observation_path,
        "--intraday-manifest-path",
        args.intraday_manifest_path,
        "--orderbook-archive-dir",
        args.orderbook_archive_dir,
        "--paper-fill-dir",
        args.paper_fill_dir,
        "--max-candidates",
        str(int(args.max_candidates)),
        "--max-entry-price",
        str(float(args.max_entry_price)),
        "--min-ask-depth",
        str(float(args.min_ask_depth)),
        "--exclude-dust",
    ]
    for station in args.station_codes or ["LTAC", "UUWW", "EGLC"]:
        program.extend(["--station-code", station])
    return program


def build_plist(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    stdout_path = Path(args.stdout_path) if args.stdout_path else repo_root / "evidence" / "logs" / "eq_dead_no_sampler.out.log"
    stderr_path = Path(args.stderr_path) if args.stderr_path else repo_root / "evidence" / "logs" / "eq_dead_no_sampler.err.log"
    return {
        "Label": args.label,
        "ProgramArguments": build_program_arguments(args),
        "StartInterval": int(args.start_interval),
        "WorkingDirectory": str(repo_root),
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "RunAtLoad": True,
    }


def _run(command: list[str], *, run=subprocess.run) -> Dict[str, Any]:
    result = run(command, text=True, capture_output=True, check=False)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def install_launchd(args: argparse.Namespace, *, run=subprocess.run) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    plist_path = Path(args.plist_path).expanduser()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_root / "evidence" / "logs").mkdir(parents=True, exist_ok=True)
    payload = build_plist(args)
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)
    uid = str(os.getuid())
    domain = f"gui/{uid}"
    target = f"{domain}/{args.label}"
    commands: list[Dict[str, Any]] = []
    bootstrap = _run(["launchctl", "bootstrap", domain, str(plist_path)], run=run)
    commands.append(bootstrap)
    installed = bootstrap["returncode"] == 0
    if not installed and "Bootstrap failed" in (bootstrap.get("stderr") or ""):
        commands.append(_run(["launchctl", "bootout", domain, str(plist_path)], run=run))
        retry = _run(["launchctl", "bootstrap", domain, str(plist_path)], run=run)
        commands.append(retry)
        installed = retry["returncode"] == 0
    if not installed and "Usage" in (bootstrap.get("stderr") or ""):
        fallback = _run(["launchctl", "load", str(plist_path)], run=run)
        commands.append(fallback)
        installed = fallback["returncode"] == 0
    commands.append(_run(["launchctl", "enable", target], run=run))
    commands.append(_run(["launchctl", "kickstart", "-k", target], run=run))
    return {
        "schema_version": "polyweather_eq_dead_no_sampler_launchd_install.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "installed": bool(installed),
        "label": args.label,
        "plist_path": str(plist_path),
        "start_interval_seconds": int(args.start_interval),
        "launchctl_status_check_command": f"launchctl print {target}",
        "log_paths": {"stdout": payload["StandardOutPath"], "stderr": payload["StandardErrorPath"]},
        "program_arguments": payload["ProgramArguments"],
        "collect_intraday_before_scan": True,
        "station_codes": args.station_codes or ["LTAC", "UUWW", "EGLC"],
        "sampler_output_path": args.summary_output,
        "commands": commands,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install paper-only eq-dead-NO sampler launchd job.")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--intraday-observation-path", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--intraday-manifest-path", default="evidence/official_observations/manifest.json")
    parser.add_argument("--orderbook-archive-dir", default="evidence/eq_dead_no")
    parser.add_argument("--paper-fill-dir", default="evidence/eq_dead_no")
    parser.add_argument("--summary-output", default="evidence/eq_dead_no/eq_dead_no_execution_sampler_report.json")
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--max-entry-price", type=float, default=0.95)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
    parser.add_argument("--start-interval", type=int, default=60)
    parser.add_argument("--python-path", default=None)
    parser.add_argument("--stdout-path", default=None)
    parser.add_argument("--stderr-path", default=None)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    parser.add_argument("--install-report-output", default="evidence/eq_dead_no/eq_dead_no_sampler_launchd_install_report.json")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = install_launchd(args)
    output = Path(args.install_report_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
