#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_LABEL = "com.polyweather.threshold-latency-sampler"


def _run(command: list[str]) -> Dict[str, Any]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uninstall threshold latency launchd job without deleting evidence.")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    plist_path = Path(args.plist_path).expanduser()
    domain = f"gui/{os.getuid()}"
    commands = [_run(["launchctl", "bootout", domain, str(plist_path)])]
    if plist_path.exists():
        plist_path.unlink()
    report = {
        "schema_version": "polyweather_threshold_latency_sampler_launchd_uninstall.v1",
        "label": args.label,
        "plist_path": str(plist_path),
        "removed_plist": not plist_path.exists(),
        "evidence_deleted": False,
        "commands": commands,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
