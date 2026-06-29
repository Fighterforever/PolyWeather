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
DEFAULT_LABEL = "com.polyweather.crypto-touch-near-miss-watcher"


def _default_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return shutil.which("python3") or sys.executable


def build_program_arguments(args: argparse.Namespace) -> list[str]:
    repo_root = Path(args.repo_root).resolve()
    return [
        str(args.python_path or _default_python(repo_root)),
        str(repo_root / "scripts" / "polymarket_alpha_crypto_touch_watcher_report.py"),
        "--python-path",
        str(args.python_path or _default_python(repo_root)),
        "--active-limit",
        str(args.active_limit),
        "--closed-limit",
        str(args.closed_limit),
        "--max-orderbook-markets",
        str(args.max_orderbook_markets),
        "--max-orderbook-tokens",
        str(args.max_orderbook_tokens),
        "--min-edge",
        str(args.min_edge),
        "--cost",
        str(args.cost),
        "--summary-output",
        args.watcher_summary_output,
    ]


def build_plist(args: argparse.Namespace) -> Dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    stdout_path = Path(args.stdout_path) if args.stdout_path else repo_root / "evidence" / "polymarket_alpha" / "logs" / "crypto_touch_watcher.out.log"
    stderr_path = Path(args.stderr_path) if args.stderr_path else repo_root / "evidence" / "polymarket_alpha" / "logs" / "crypto_touch_watcher.err.log"
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
    (repo_root / "evidence" / "polymarket_alpha" / "logs").mkdir(parents=True, exist_ok=True)
    payload = build_plist(args)
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)

    uid = str(os.getuid())
    domain = f"gui/{uid}"
    target = f"{domain}/{args.label}"
    commands: list[Dict[str, Any]] = []
    installed = False
    if not bool(args.no_install):
        bootstrap = _run(["launchctl", "bootstrap", domain, str(plist_path)], run=run)
        commands.append(bootstrap)
        installed = bootstrap["returncode"] == 0
        if not installed and "Bootstrap failed" in (bootstrap.get("stderr") or ""):
            commands.append(_run(["launchctl", "bootout", domain, str(plist_path)], run=run))
            retry = _run(["launchctl", "bootstrap", domain, str(plist_path)], run=run)
            commands.append(retry)
            installed = retry["returncode"] == 0
        if not installed:
            fallback = _run(["launchctl", "load", str(plist_path)], run=run)
            commands.append(fallback)
            installed = fallback["returncode"] == 0
        commands.append(_run(["launchctl", "enable", target], run=run))
        commands.append(_run(["launchctl", "kickstart", "-k", target], run=run))

    return {
        "schema_version": "polyweather_polymarket_alpha_crypto_touch_watcher_launchd_install.v1",
        "scope": "polymarket_only",
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
            "install_report": args.install_report_output,
            "watcher_summary": args.watcher_summary_output,
            "near_miss_watch": "evidence/polymarket_alpha/crypto_touch_near_miss_watch.jsonl",
            "near_miss_orderbook_snapshots": "evidence/polymarket_alpha/crypto_touch_near_miss_orderbook_snapshots.jsonl",
        },
        "commands": commands,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install paper-only crypto touch near-miss watcher launchd job.")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--start-interval", type=int, default=60)
    parser.add_argument("--active-limit", type=int, default=3000)
    parser.add_argument("--closed-limit", type=int, default=0)
    parser.add_argument("--max-orderbook-markets", type=int, default=80)
    parser.add_argument("--max-orderbook-tokens", type=int, default=120)
    parser.add_argument("--min-edge", type=float, default=0.01)
    parser.add_argument("--cost", type=float, default=0.01)
    parser.add_argument("--watcher-summary-output", default="evidence/polymarket_alpha/crypto_touch_watcher_run_report.json")
    parser.add_argument("--python-path", default=None)
    parser.add_argument("--stdout-path", default=None)
    parser.add_argument("--stderr-path", default=None)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    parser.add_argument("--install-report-output", default="evidence/polymarket_alpha/crypto_touch_watcher_launchd_install_report.json")
    parser.add_argument("--no-install", action="store_true")
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
