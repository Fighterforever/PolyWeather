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

from src.trading.polymarket_alpha.active_probability_edge_scanner import (  # noqa: E402
    build_formal_fill_followup_snapshot_coverage_report,
    load_jsonl,
    write_json,
)


DEFAULT_ROOT = Path("evidence/polymarket_alpha")
DEFAULT_PAPER = DEFAULT_ROOT / "probability_edge_paper"


def _watcher_status(label: str = "com.polyweather.crypto-touch-near-miss-watcher") -> dict:
    command = ["launchctl", "print", f"gui/{__import__('os').getuid()}/{label}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:  # pragma: no cover - platform dependent
        return {"state": "unknown", "error": str(exc), "command": " ".join(command)}
    state = "not_loaded"
    pid = None
    for line in result.stdout.splitlines() + result.stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("state ="):
            state = stripped.split("=", 1)[1].strip()
        if stripped.startswith("pid ="):
            pid = stripped.split("=", 1)[1].strip()
    return {
        "state": state,
        "pid": pid,
        "returncode": result.returncode,
        "command": " ".join(command),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit crypto touch formal fill follow-up orderbook snapshot coverage.")
    parser.add_argument("--fills", default=str(DEFAULT_PAPER / "fills.jsonl"))
    parser.add_argument("--followup-snapshots", default=str(DEFAULT_PAPER / "fill_followup_orderbook_snapshots.jsonl"))
    parser.add_argument("--summary-output", default=str(DEFAULT_ROOT / "crypto_touch_followup_snapshot_coverage_report.json"))
    parser.add_argument("--watcher-label", default="com.polyweather.crypto-touch-near-miss-watcher")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_formal_fill_followup_snapshot_coverage_report(
        fills=load_jsonl(args.fills),
        followup_snapshots=load_jsonl(args.followup_snapshots),
        snapshot_path=args.followup_snapshots,
        watcher_status=_watcher_status(args.watcher_label),
    )
    report["artifact_paths"] = {
        "fills": str(args.fills),
        "followup_snapshots": str(args.followup_snapshots),
    }
    write_json(args.summary_output, report)
    print(
        json.dumps(
            {
                "fill_count": report.get("fill_count"),
                "followup_snapshot_count": report.get("followup_snapshot_count"),
                "coverage_by_horizon": {
                    key: {
                        "covered_fill_count": value.get("covered_fill_count"),
                        "missing_fill_count": value.get("missing_fill_count"),
                        "eligible_elapsed_fill_count": value.get("eligible_elapsed_fill_count"),
                        "gap_reason_counts": value.get("gap_reason_counts"),
                    }
                    for key, value in (report.get("coverage_by_horizon") or {}).items()
                },
                "watcher_state": (report.get("watcher_status") or {}).get("state"),
                "live_order_path": report.get("live_order_path"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
