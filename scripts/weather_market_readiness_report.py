#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.weather_live_readiness import (  # noqa: E402
    DEFAULT_BACKFILL_DIR,
    build_live_readiness_report,
    dump_readiness_report,
    load_signal_report,
)
from src.trading.weather_current_signal import (  # noqa: E402
    default_current_signal_report_dir,
    load_latest_current_signal_report,
)
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_strict_gate_queue import default_strict_gate_queue_dir  # noqa: E402


def _load_json_report(path: str | None) -> dict | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _load_orderbook_archive_coverage_report(path: str | None) -> dict | None:
    payload = _load_json_report(path)
    if not isinstance(payload, dict):
        return None
    nested = payload.get("orderbook_archive_coverage")
    if isinstance(nested, dict):
        return nested
    nested = payload.get("orderbook_archive_coverage_report")
    if isinstance(nested, dict):
        return nested
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize paper evidence and live readiness for weather market trading.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument(
        "--signal-report",
        default=None,
        help="Optional JSON report from weather_market_signal_report.py for current signal availability.",
    )
    parser.add_argument(
        "--current-signal-report-dir",
        default=None,
        help="Directory containing latest_signal_report.json. Defaults to <paper-journal-dir>/current_signal_reports.",
    )
    parser.add_argument(
        "--no-current-signal-report-auto",
        action="store_true",
        help="Do not auto-load latest current signal report when --signal-report is omitted.",
    )
    parser.add_argument(
        "--strict-gate-queue-dir",
        default=None,
        help="Directory containing strict-gate targeted queue JSONL. Defaults to <paper-journal-dir>/strict_gate_queues.",
    )
    parser.add_argument(
        "--strict-gate-replay-report",
        default=None,
        help="Optional JSON report from weather_strict_gate_replay_report.py for no-lookahead replay hard gates.",
    )
    parser.add_argument(
        "--settlement-calibration-report",
        default=None,
        help="Optional JSON report from weather_settlement_calibration_report.py for settlement calibration hard gates.",
    )
    parser.add_argument(
        "--live-evidence-bundle-report",
        default=None,
        help="Optional JSON report from weather_live_evidence_bundle_report.py; used as replay/calibration inputs unless explicit reports are supplied.",
    )
    parser.add_argument(
        "--orderbook-archive-coverage-report",
        default=None,
        help=(
            "Optional JSON report for active orderbook archive coverage. May point to either "
            "a standalone coverage report or a full weather_market_paper_cycle JSON containing "
            "orderbook_archive_coverage."
        ),
    )
    parser.add_argument(
        "--live-permission",
        action="store_true",
        help="Mark explicit live permission as present. This does not submit orders.",
    )
    parser.add_argument(
        "--include-quarantine-surface",
        action="store_true",
        help="Attach compact paper-only quarantine surface diagnostics without counting them toward the live gate.",
    )
    parser.add_argument(
        "--include-temperature-taker-validation",
        action="store_true",
        help="Attach paper-only formal taker validation diagnostics without counting them toward the live gate.",
    )
    parser.add_argument("--temperature-taker-journal-dir", default="data/trading/weather_temperature_taker_paper")
    parser.add_argument("--quarantine-journal-dir", default="data/trading/weather_quarantine_paper")
    parser.add_argument("--quarantine-surface-min-decision-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-promote-count", type=int, default=5)
    parser.add_argument("--quarantine-surface-min-mean-markout-cents", type=float, default=0.0)
    parser.add_argument("--quarantine-surface-min-win-rate", type=float, default=0.55)
    parser.add_argument("--quarantine-surface-min-maker-quote-count", type=int, default=0)
    parser.add_argument("--quarantine-surface-min-maker-mean-markout-cents", type=float, default=0.0)
    parser.add_argument(
        "--settlement-grace-hours",
        type=float,
        default=24.0,
        help="Hours after market end to wait before treating unresolved fills as overdue.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    signal_report = load_signal_report(args.signal_report)
    if signal_report is None and not bool(args.no_current_signal_report_auto):
        signal_report = load_latest_current_signal_report(
            report_dir=args.current_signal_report_dir
            or default_current_signal_report_dir(args.paper_journal_dir)
        )
    live_evidence_bundle_report = _load_json_report(args.live_evidence_bundle_report)
    strict_gate_replay_report = _load_json_report(args.strict_gate_replay_report)
    settlement_calibration_report = _load_json_report(args.settlement_calibration_report)
    orderbook_archive_coverage_report = _load_orderbook_archive_coverage_report(
        args.orderbook_archive_coverage_report
    )
    if isinstance(live_evidence_bundle_report, dict):
        if strict_gate_replay_report is None:
            nested_replay = live_evidence_bundle_report.get("strict_gate_replay_report")
            strict_gate_replay_report = nested_replay if isinstance(nested_replay, dict) else None
        if settlement_calibration_report is None:
            nested_calibration = live_evidence_bundle_report.get("settlement_calibration_report")
            settlement_calibration_report = nested_calibration if isinstance(nested_calibration, dict) else None
        if orderbook_archive_coverage_report is None:
            nested_coverage = live_evidence_bundle_report.get("orderbook_archive_coverage_report")
            orderbook_archive_coverage_report = nested_coverage if isinstance(nested_coverage, dict) else None
    report = build_live_readiness_report(
        journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        quarantine_journal_dir=args.quarantine_journal_dir,
        signal_report=signal_report,
        strict_gate_replay_report=strict_gate_replay_report,
        settlement_calibration_report=settlement_calibration_report,
        orderbook_archive_coverage_report=orderbook_archive_coverage_report,
        include_quarantine_surface=bool(args.include_quarantine_surface),
        include_temperature_taker_validation=bool(args.include_temperature_taker_validation),
        temperature_taker_journal_dir=args.temperature_taker_journal_dir,
        strict_gate_queue_dir=args.strict_gate_queue_dir
        or default_strict_gate_queue_dir(args.paper_journal_dir),
        live_permission=bool(args.live_permission),
        settlement_grace_hours=float(args.settlement_grace_hours),
        quarantine_surface_min_decision_count=args.quarantine_surface_min_decision_count,
        quarantine_surface_min_promote_count=args.quarantine_surface_min_promote_count,
        quarantine_surface_min_mean_markout_cents=args.quarantine_surface_min_mean_markout_cents,
        quarantine_surface_min_win_rate=args.quarantine_surface_min_win_rate,
        quarantine_surface_min_maker_quote_count=args.quarantine_surface_min_maker_quote_count,
        quarantine_surface_min_maker_mean_markout_cents=args.quarantine_surface_min_maker_mean_markout_cents,
    )
    print(dump_readiness_report(report))


if __name__ == "__main__":
    main()
