#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_observation_lock_signal import build_observation_lock_signal_report  # noqa: E402
from src.trading.weather_paper_journal import load_jsonl, utc_now_iso  # noqa: E402
from src.weather.weather_observations import OfficialIntradayObservationRepository  # noqa: E402


def _read_json(path: Optional[str | Path]) -> Dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows_from_payload(path: Optional[str | Path]) -> list[dict]:
    payload = _read_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if rows is None:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _archive_rows(path: str | Path) -> list[dict]:
    archive_root = Path(path)
    return load_jsonl(archive_root / "orderbook_snapshots.jsonl")


def _write_json(path: Optional[str | Path], payload: Dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def build_report_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    rows = _rows_from_payload(args.rows_json)
    if not rows and args.orderbook_archive_dir:
        rows = _archive_rows(args.orderbook_archive_dir)
    observation_path = args.intraday_observation_path or args.observation_jsonl
    repository = OfficialIntradayObservationRepository(observation_path) if observation_path else None
    observations = load_jsonl(args.observation_jsonl) if args.observation_jsonl and not repository else []
    return build_observation_lock_signal_report(
        rows,
        observations=observations,
        intraday_repository=repository,
        generated_at=args.generated_at or utc_now_iso(),
        min_executable_edge=float(args.min_executable_edge),
        max_spread=float(args.max_spread),
        min_ask_depth=float(args.min_ask_depth),
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a paper-only weather observation-lock alpha signal report.")
    parser.add_argument("--rows-json", default=None, help="JSON payload with rows. If omitted, orderbook archive rows are used.")
    parser.add_argument("--orderbook-archive-dir", default="evidence/orderbook_archive")
    parser.add_argument("--observation-jsonl", default=None)
    parser.add_argument("--intraday-observation-path", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--replay-time", dest="generated_at", default=None)
    parser.add_argument("--paper-only", action="store_true", default=True)
    parser.add_argument("--min-executable-edge", type=float, default=0.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-ask-depth", type=float, default=1.0)
    parser.add_argument("--summary-output", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    report = build_report_from_args(args)
    _write_json(args.summary_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
