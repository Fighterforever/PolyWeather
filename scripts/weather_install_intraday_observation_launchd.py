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
DEFAULT_LABEL = "com.polyweather.intraday-observation-collector"


def _default_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return shutil.which("python3") or sys.executable


def build_program_arguments(args: argparse.Namespace) -> list[str]:
    repo_root = Path(args.repo_root).resolve()
    program = [
        str(args.python_path or _default_python(repo_root)),
        str(repo_root / "scripts" / "weather_collect_intraday_observations.py"),
    ]
    for station in args.station_codes or ["LTAC", "UUWW", "EGLC"]:
        program.extend(["--station-code", station])
    program.extend(["--output", args.output, "--manifest", args.manifest])
    return program


def build_plist(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    stdout_path = Path(args.stdout_path) if args.stdout_path else repo_root / "evidence" / "logs" / "intraday_observation_collector.out.log"
    stderr_path = Path(args.stderr_path) if args.stderr_path else repo_root / "evidence" / "logs" / "intraday_observation_collector.err.log"
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
    commands = [_run(["launchctl", "bootstrap", domain, str(plist_path)], run=run)]
    installed = commands[-1]["returncode"] == 0
    if not installed and "Bootstrap failed" in (commands[-1].get("stderr") or ""):
        commands.append(_run(["launchctl", "bootout", domain, str(plist_path)], run=run))
        commands.append(_run(["launchctl", "bootstrap", domain, str(plist_path)], run=run))
        installed = commands[-1]["returncode"] == 0
    commands.append(_run(["launchctl", "enable", target], run=run))
    commands.append(_run(["launchctl", "kickstart", "-k", target], run=run))
    return {
        "schema_version": "polyweather_intraday_observation_launchd_install.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "installed": bool(installed),
        "label": args.label,
        "plist_path": str(plist_path),
        "start_interval_seconds": int(args.start_interval),
        "station_codes": args.station_codes or ["LTAC", "UUWW", "EGLC"],
        "output_path": args.output,
        "manifest_path": args.manifest,
        "log_paths": {"stdout": payload["StandardOutPath"], "stderr": payload["StandardErrorPath"]},
        "launchctl_status_check_command": f"launchctl print {target}",
        "program_arguments": payload["ProgramArguments"],
        "commands": commands,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install paper-only intraday METAR observation collector launchd job.")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--station-code", action="append", dest="station_codes", default=None)
    parser.add_argument("--output", default="evidence/official_observations/intraday_observations.jsonl")
    parser.add_argument("--manifest", default="evidence/official_observations/manifest.json")
    parser.add_argument("--start-interval", type=int, default=300)
    parser.add_argument("--python-path", default=None)
    parser.add_argument("--stdout-path", default=None)
    parser.add_argument("--stderr-path", default=None)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    print(json.dumps(install_launchd(parse_args(argv)), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
