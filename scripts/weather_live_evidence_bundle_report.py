#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR  # noqa: E402
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_live_evidence_bundle import (  # noqa: E402
    build_live_evidence_bundle_report_from_dirs,
)
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR  # noqa: E402
from src.trading.weather_strict_gate_queue import default_strict_gate_queue_dir  # noqa: E402


def _summary_only(report: dict) -> dict:
    trimmed = dict(report)
    strict = dict(trimmed.get("strict_gate_replay_report") or {})
    replay = dict(strict.get("replay") or {})
    replay.pop("fills", None)
    strict["replay"] = replay
    trimmed["strict_gate_replay_report"] = strict

    historical = dict(trimmed.get("historical_evidence_report") or {})
    historical.pop("supplements", None)
    historical.pop("gap_samples", None)
    trimmed["historical_evidence_report"] = historical

    strict_evidence = dict(trimmed.get("strict_gate_historical_evidence_report") or {})
    strict_evidence.pop("supplements", None)
    strict_evidence.pop("gap_samples", None)
    trimmed["strict_gate_historical_evidence_report"] = strict_evidence

    closed_replay = dict(trimmed.get("closed_historical_replay_report") or {})
    closed_replay.pop("candidates", None)
    closed_replay.pop("orderbook_snapshots", None)
    closed_replay_payload = dict(closed_replay.get("replay") or {})
    closed_replay_payload.pop("fills", None)
    closed_replay["replay"] = closed_replay_payload
    closed_evidence = dict(closed_replay.get("historical_evidence") or {})
    closed_evidence.pop("supplements", None)
    closed_evidence.pop("gap_samples", None)
    closed_replay["historical_evidence"] = closed_evidence
    trimmed["closed_historical_replay_report"] = closed_replay

    preresolution_replay = dict(trimmed.get("preresolution_orderbook_replay_report") or {})
    preresolution_replay.pop("candidates", None)
    preresolution_replay.pop("orderbook_snapshots", None)
    preresolution_payload = dict(preresolution_replay.get("replay") or {})
    preresolution_payload.pop("fills", None)
    preresolution_replay["replay"] = preresolution_payload
    preresolution_evidence = dict(preresolution_replay.get("historical_evidence") or {})
    preresolution_evidence.pop("supplements", None)
    preresolution_evidence.pop("gap_samples", None)
    preresolution_replay["historical_evidence"] = preresolution_evidence
    trimmed["preresolution_orderbook_replay_report"] = preresolution_replay

    closed_token_coverage = dict(trimmed.get("orderbook_closed_token_coverage_report") or {})
    closed_token_coverage.pop("matched_samples", None)
    closed_token_coverage.pop("closed_markets_missing_archive_samples", None)
    closed_token_coverage.pop("archived_markets_pending_closed_backfill_samples", None)
    closed_token_coverage.pop("closed_missing_token_samples", None)
    followup = dict(closed_token_coverage.get("closed_backfill_followup_plan") or {})
    followup.pop("market_queries", None)
    followup.pop("next_await_market_queries", None)
    followup.pop("next_market_close_queries", None)
    followup.pop("next_observation_window_end_queries", None)
    followup.pop("next_settlement_due_queries", None)
    followup.pop("next_due_market_queries", None)
    followup.pop("recommended_command", None)
    followup.pop("requests", None)
    followup.pop("await_market_end_samples", None)
    followup.pop("awaiting_market_close_samples", None)
    followup.pop("awaiting_observation_window_end_samples", None)
    followup.pop("awaiting_settlement_due_time_samples", None)
    followup.pop("wrong_due_prevented_samples", None)
    followup.pop("closed_backfill_attempted_but_market_open_samples", None)
    followup.pop("missing_settlement_due_time_metadata_samples", None)
    followup.pop("missing_end_time_samples", None)
    closed_token_coverage["closed_backfill_followup_plan"] = followup
    trimmed["orderbook_closed_token_coverage_report"] = closed_token_coverage

    official_values = dict(trimmed.get("official_value_backfill_report") or {})
    official_values.pop("supplements", None)
    official_values.pop("gap_samples", None)
    trimmed["official_value_backfill_report"] = official_values

    calibration = dict(trimmed.get("settlement_calibration_report") or {})
    calibration.pop("calibration_rows", None)
    calibration.pop("mismatch_samples", None)
    calibration.pop("gap_samples", None)
    trimmed["settlement_calibration_report"] = calibration
    return trimmed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one paper-only live-evidence bundle from strict replay and settlement calibration.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument(
        "--strict-gate-queue-dir",
        default=None,
        help="Directory containing strict-gate queue JSONL. Defaults to <paper-journal-dir>/strict_gate_queues.",
    )
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ORDERBOOK_ARCHIVE_DIR))
    parser.add_argument(
        "--official-value-supplements",
        help="JSON/JSONL official final value supplements generated by weather_official_value_backfill_report.py.",
    )
    parser.add_argument(
        "--fetch-external-official-values",
        action="store_true",
        help="Explicitly fetch supported external official-value sources during bundle build. Defaults to local/existing records only.",
    )
    parser.add_argument(
        "--allow-wunderground-proxy",
        action="store_true",
        help="Allow Wunderground as a diagnostic proxy for non-Wunderground settlement sources. Disabled by default.",
    )
    parser.add_argument("--max-official-value-gap-samples", type=int, default=10)
    parser.add_argument("--max-official-value-backfill-plan-requests", type=int, default=20)
    parser.add_argument("--replay-time", default=None)
    parser.add_argument("--size", type=float, default=1.0)
    parser.add_argument(
        "--queue-name",
        action="append",
        dest="queue_names",
        help="Restrict replay to one or more strict-gate queue names.",
    )
    parser.add_argument("--min-official-truth-samples", type=int, default=30)
    parser.add_argument("--min-probability-score-samples", type=int, default=30)
    parser.add_argument("--min-resolved-pnl-samples", type=int, default=10)
    parser.add_argument("--min-official-truth-coverage", type=float, default=0.80)
    parser.add_argument("--max-sample-rows", type=int, default=20)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Drop replay fills, historical supplements, and row-level calibration samples from stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_live_evidence_bundle_report_from_dirs(
        paper_journal_dir=args.paper_journal_dir,
        backfill_dir=args.backfill_dir,
        strict_gate_queue_dir=args.strict_gate_queue_dir
        or default_strict_gate_queue_dir(args.paper_journal_dir),
        orderbook_archive_dir=args.orderbook_archive_dir,
        official_value_supplements_path=args.official_value_supplements,
        replay_time=args.replay_time,
        size=float(args.size),
        queue_names=args.queue_names,
        fetch_external_official_values=args.fetch_external_official_values,
        allow_wunderground_proxy=args.allow_wunderground_proxy,
        max_official_value_gap_samples=args.max_official_value_gap_samples,
        max_official_value_backfill_plan_requests=args.max_official_value_backfill_plan_requests,
        min_official_truth_samples=args.min_official_truth_samples,
        min_probability_score_samples=args.min_probability_score_samples,
        min_resolved_pnl_samples=args.min_resolved_pnl_samples,
        min_official_truth_coverage=args.min_official_truth_coverage,
        max_sample_rows=args.max_sample_rows,
    )
    if args.summary_only:
        report = _summary_only(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
