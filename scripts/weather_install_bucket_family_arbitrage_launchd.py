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
DEFAULT_LABEL = "com.polyweather.bucket-family-arbitrage-sampler"


def _default_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return shutil.which("python3") or sys.executable


def build_program_arguments(args: argparse.Namespace) -> list[str]:
    repo_root = Path(args.repo_root).resolve()
    return [
        str(args.python_path or _default_python(repo_root)),
        str(repo_root / "scripts" / "weather_bucket_family_arbitrage_report.py"),
        "--paper-only",
        "--orderbook-archive-dir",
        args.orderbook_archive_dir,
        "--paper-fill-dir",
        args.paper_fill_dir,
        "--min-edge-cents",
        str(args.min_edge_cents),
        "--min-leg-depth",
        str(args.min_leg_depth),
        "--max-candidates",
        str(args.max_candidates),
        "--summary-output",
        args.summary_output,
        "--candidates-output",
        args.candidates_output,
        "--watch-output",
        args.watch_output,
        "--monotonic-candidates-output",
        args.monotonic_candidates_output,
    ]


def build_plist(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    stdout_path = Path(args.stdout_path) if args.stdout_path else repo_root / "evidence" / "bucket_family" / "logs" / "arbitrage_sampler.out.log"
    stderr_path = Path(args.stderr_path) if args.stderr_path else repo_root / "evidence" / "bucket_family" / "logs" / "arbitrage_sampler.err.log"
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
    (repo_root / "evidence" / "bucket_family" / "logs").mkdir(parents=True, exist_ok=True)
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
        "schema_version": "polyweather_bucket_family_arbitrage_launchd_install.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "label": args.label,
        "plist_path": str(plist_path),
        "installed": bool(installed),
        "start_interval_seconds": int(args.start_interval),
        "launchctl_status_check_command": f"launchctl print {target}",
        "log_paths": {
            "stdout": payload["StandardOutPath"],
            "stderr": payload["StandardErrorPath"],
        },
        "program_arguments": payload["ProgramArguments"],
        "artifact_paths": {
            "summary_output": args.summary_output,
            "candidates_output": args.candidates_output,
            "watch_output": args.watch_output,
            "paper_fill_dir": args.paper_fill_dir,
        },
        "commands": commands,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install paper-only bucket-family arbitrage sampler launchd job.")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--start-interval", type=int, default=60)
    parser.add_argument("--orderbook-archive-dir", default="evidence/bucket_family/orderbook_archive")
    parser.add_argument("--paper-fill-dir", default="evidence/bucket_family/basket_paper")
    parser.add_argument("--min-edge-cents", type=float, default=1.0)
    parser.add_argument("--min-leg-depth", type=float, default=1.0)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--summary-output", default="evidence/bucket_family/latest_arbitrage_report.json")
    parser.add_argument("--candidates-output", default="evidence/bucket_family/basket_arbitrage_candidates.jsonl")
    parser.add_argument("--watch-output", default="evidence/bucket_family/basket_arbitrage_watch_rows.jsonl")
    parser.add_argument("--monotonic-candidates-output", default="evidence/bucket_family/monotonic_arbitrage_candidates.jsonl")
    parser.add_argument("--python-path", default=None)
    parser.add_argument("--stdout-path", default=None)
    parser.add_argument("--stderr-path", default=None)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    parser.add_argument("--install-report-output", default="evidence/bucket_family/bucket_family_arbitrage_launchd_install_report.json")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = install_launchd(args)
    output = Path(args.install_report_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
