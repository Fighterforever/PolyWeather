#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(command: List[str]) -> Dict[str, Any]:
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper-only crypto touch near-miss watcher.")
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--active-limit", type=int, default=3000)
    parser.add_argument("--closed-limit", type=int, default=0)
    parser.add_argument("--max-orderbook-markets", type=int, default=80)
    parser.add_argument("--max-orderbook-tokens", type=int, default=120)
    parser.add_argument("--min-edge", type=float, default=0.01)
    parser.add_argument("--cost", type=float, default=0.01)
    parser.add_argument("--summary-output", default="evidence/polymarket_alpha/crypto_touch_watcher_run_report.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    commands = [
        [
            args.python_path,
            "scripts/polymarket_alpha_market_discovery_report.py",
            "--active-limit",
            str(args.active_limit),
            "--closed-limit",
            str(args.closed_limit),
            "--include-order-books",
            "--max-orderbook-markets",
            str(args.max_orderbook_markets),
            "--generated-at",
            generated_at,
        ],
        [
            args.python_path,
            "scripts/polymarket_alpha_crypto_edge_report.py",
            "--fetch-gamma-metadata",
            "--fetch-binance-spot",
            "--fetch-orderbooks",
            "--max-orderbook-tokens",
            str(args.max_orderbook_tokens),
            "--min-edge",
            str(args.min_edge),
            "--cost",
            str(args.cost),
            "--generated-at",
            generated_at,
        ],
        [
            args.python_path,
            "scripts/polymarket_alpha_active_probability_edge_report.py",
            "--min-edge",
            str(args.min_edge),
            "--cost",
            str(args.cost),
            "--generated-at",
            generated_at,
        ],
        [
            args.python_path,
            "scripts/polymarket_alpha_crypto_touch_watch_markout_report.py",
        ],
    ]
    results = [_run(command) for command in commands]
    report = {
        "schema_version": "polyweather_polymarket_alpha_crypto_touch_watcher.v1",
        "scope": "polymarket_only",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at,
        "success": all(result["returncode"] == 0 for result in results),
        "commands": results,
        "artifact_paths": {
            "crypto_probability_edge_report": "evidence/polymarket_alpha/crypto_probability_edge_report.json",
            "active_probability_edge_report": "evidence/polymarket_alpha/active_probability_edge_report.json",
            "probability_edge_fills": "evidence/polymarket_alpha/probability_edge_paper/fills.jsonl",
            "crypto_touch_near_miss_watch": "evidence/polymarket_alpha/crypto_touch_near_miss_watch.jsonl",
            "crypto_touch_near_miss_orderbook_snapshots": "evidence/polymarket_alpha/crypto_touch_near_miss_orderbook_snapshots.jsonl",
            "crypto_touch_near_miss_markout_report": "evidence/polymarket_alpha/crypto_touch_near_miss_markout_report.json",
        },
    }
    output = PROJECT_ROOT / args.summary_output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
