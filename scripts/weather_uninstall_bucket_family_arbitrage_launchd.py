#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_LABEL = "com.polyweather.bucket-family-arbitrage-sampler"


def _run(command: list[str], *, run=subprocess.run) -> Dict[str, Any]:
    result = run(command, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def uninstall_launchd(args: argparse.Namespace, *, run=subprocess.run) -> Dict[str, Any]:
    plist_path = Path(args.plist_path).expanduser()
    uid = str(os.getuid())
    domain = f"gui/{uid}"
    commands = [_run(["launchctl", "bootout", domain, str(plist_path)], run=run)]
    removed = False
    if plist_path.exists():
        plist_path.unlink()
        removed = True
    return {
        "schema_version": "polyweather_bucket_family_arbitrage_launchd_uninstall.v1",
        "paper_only": True,
        "live_order_path": False,
        "label": args.label,
        "plist_path": str(plist_path),
        "plist_removed": removed,
        "commands": commands,
        "evidence_artifacts_deleted": False,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uninstall paper-only bucket-family arbitrage sampler launchd job.")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    print(json.dumps(uninstall_launchd(parse_args(argv)), ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
