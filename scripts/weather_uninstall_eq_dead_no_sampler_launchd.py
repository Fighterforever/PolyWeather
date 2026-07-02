#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional


DEFAULT_LABEL = "com.polyweather.eq-dead-no-sampler"


def uninstall_launchd(args: argparse.Namespace, *, run=subprocess.run) -> Dict[str, object]:
    plist_path = Path(args.plist_path).expanduser()
    uid = str(os.getuid())
    domain = f"gui/{uid}"
    commands = []
    bootout = run(["launchctl", "bootout", domain, str(plist_path)], text=True, capture_output=True, check=False)
    commands.append({"command": ["launchctl", "bootout", domain, str(plist_path)], "returncode": bootout.returncode, "stdout": bootout.stdout, "stderr": bootout.stderr})
    removed = False
    if plist_path.exists():
        plist_path.unlink()
        removed = True
    return {
        "schema_version": "polyweather_eq_dead_no_sampler_launchd_uninstall.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "label": args.label,
        "plist_path": str(plist_path),
        "plist_removed": removed,
        "commands": commands,
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Uninstall paper-only eq-dead-NO sampler launchd job.")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--plist-path", default=str(Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    report = uninstall_launchd(parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
