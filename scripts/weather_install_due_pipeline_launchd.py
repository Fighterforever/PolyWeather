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
DEFAULT_LABEL = "com.polyweather.due-evidence-pipeline"
DEFAULT_CONFIRM = "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH"


def _default_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return shutil.which("python3") or sys.executable


def _as_list(values: Optional[list[str]], *, default: list[str]) -> list[str]:
    return list(values) if values else list(default)


def build_program_arguments(args: argparse.Namespace) -> list[str]:
    repo_root = Path(args.repo_root).resolve()
    program = [
        str(args.python_path or _default_python(repo_root)),
        str(repo_root / "scripts" / "weather_guarded_due_pipeline_runner.py"),
        "--run-after-utc",
        args.run_after_utc,
        "--paper-journal-dir",
        args.paper_journal_dir,
        "--strict-gate-queue-dir",
        args.strict_gate_queue_dir,
        "--orderbook-archive-dir",
        args.orderbook_archive_dir,
        "--backfill-dir",
        args.backfill_dir,
        "--summary-output",
        args.summary_output,
        "--lock-file",
        args.lock_file,
        "--done-file",
        args.done_file,
        "--confirm",
        args.confirm,
    ]
    for source in _as_list(args.include_settlement_sources, default=["metar"]):
        program.extend(["--include-settlement-source", source])
    for station in _as_list(args.include_station_codes, default=["LTAC", "UUWW"]):
        program.extend(["--include-station-code", station])
    for slug in _as_list(args.include_market_slugs, default=[]):
        program.extend(["--include-market-slug", slug])
    if args.allow_partial_official_truth:
        program.append("--allow-partial-official-truth")
    if args.fetch_external_official_values:
        program.append("--fetch-external-official-values")
    return program


def build_plist(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    stdout_path = Path(args.stdout_path) if args.stdout_path else repo_root / "evidence" / "logs" / "due_pipeline_launchd.out.log"
    stderr_path = Path(args.stderr_path) if args.stderr_path else repo_root / "evidence" / "logs" / "due_pipeline_launchd.err.log"
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
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def install_launchd(args: argparse.Namespace, *, run=subprocess.run) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    plist_path = Path(args.plist_path).expanduser()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_root / "evidence" / "logs").mkdir(parents=True, exist_ok=True)
    payload = build_plist(args)
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)

    uid = str(os.getuid())
    target = f"gui/{uid}/{args.label}"
    domain = f"gui/{uid}"
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

    enable = _run(["launchctl", "enable", target], run=run)
    kickstart = _run(["launchctl", "kickstart", "-k", target], run=run)
    commands.extend([enable, kickstart])

    return {
        "schema_version": "polyweather_due_pipeline_launchd_install.v1",
        "paper_only": True,
        "live_order_path": False,
        "plist_path": str(plist_path),
        "label": args.label,
        "run_after_utc": args.run_after_utc,
        "installed": bool(installed),
        "start_interval_seconds": int(args.start_interval),
        "launchctl_status_check_command": f"launchctl print {target}",
        "log_paths": {
            "stdout": payload["StandardOutPath"],
            "stderr": payload["StandardErrorPath"],
        },
        "done_file": args.done_file,
        "lock_file": args.lock_file,
        "program_arguments": payload["ProgramArguments"],
        "commands": commands,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the local paper-only weather due pipeline launchd job.")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--run-after-utc", default="2026-06-28T03:05:00Z")
    parser.add_argument("--paper-journal-dir", default="evidence/paper_run")
    parser.add_argument("--strict-gate-queue-dir", default="evidence/strict_gate_queues")
    parser.add_argument("--orderbook-archive-dir", default="evidence/orderbook_archive")
    parser.add_argument("--backfill-dir", default="evidence/weather_backfill_local")
    parser.add_argument("--summary-output", default="evidence/due_pipeline_metar_ltac_uuww_after_due.json")
    parser.add_argument("--lock-file", default="evidence/.due_pipeline_metar_ltac_uuww.lock")
    parser.add_argument("--done-file", default="evidence/.due_pipeline_metar_ltac_uuww.done")
    parser.add_argument("--include-station-code", action="append", dest="include_station_codes", default=None)
    parser.add_argument("--include-settlement-source", action="append", dest="include_settlement_sources", default=None)
    parser.add_argument("--include-market-slug", action="append", dest="include_market_slugs", default=None)
    parser.add_argument("--confirm", default=DEFAULT_CONFIRM)
    parser.add_argument("--allow-partial-official-truth", action="store_true", default=True)
    parser.add_argument("--no-allow-partial-official-truth", action="store_false", dest="allow_partial_official_truth")
    parser.add_argument("--fetch-external-official-values", action="store_true", default=True)
    parser.add_argument("--no-fetch-external-official-values", action="store_false", dest="fetch_external_official_values")
    parser.add_argument("--start-interval", type=int, default=300)
    parser.add_argument("--python-path", default=None)
    parser.add_argument("--stdout-path", default=None)
    parser.add_argument("--stderr-path", default=None)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    report = install_launchd(parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
