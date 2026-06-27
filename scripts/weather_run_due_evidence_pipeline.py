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

from scripts.weather_archived_orderbook_due_refresh_report import (  # noqa: E402
    CONFIRM_TOKEN,
    build_due_refresh_report,
)
from src.trading.polymarket_orderbook_archive import DEFAULT_ORDERBOOK_ARCHIVE_DIR  # noqa: E402
from src.trading.weather_closed_backfill import DEFAULT_BACKFILL_DIR  # noqa: E402
from src.trading.weather_closed_replay_seed import load_closed_backfill_records_with_snapshot_supplements  # noqa: E402
from src.trading.weather_historical_evidence import historical_evidence_from_replay_report  # noqa: E402
from src.trading.weather_live_evidence_bundle import (  # noqa: E402
    _archived_yes_token_ids,
    _closed_records_with_archived_overlap,
    build_live_evidence_bundle_report,
)
from src.trading.weather_live_readiness import build_live_readiness_report  # noqa: E402
from src.trading.weather_orderbook_archive_coverage import build_orderbook_closed_token_coverage_report  # noqa: E402
from src.trading.weather_paper_journal import DEFAULT_PAPER_JOURNAL_DIR, load_jsonl  # noqa: E402
from src.trading.weather_settlement_calibration import build_settlement_calibration_report  # noqa: E402
from src.trading.weather_settlement_truth_audit import build_settlement_truth_audit_report  # noqa: E402
from src.trading.weather_strict_gate_queue import default_strict_gate_queue_dir  # noqa: E402
from src.trading.weather_strict_gate_replay import build_strict_gate_replay_report_from_dirs  # noqa: E402
from src.weather.official_value_backfill import (  # noqa: E402
    apply_official_value_supplements,
    build_official_value_backfill_report,
)


SCHEMA_VERSION = "polyweather_weather_due_evidence_pipeline.v1"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows if isinstance(row, dict)),
        encoding="utf-8",
    )


def _load_orderbooks(orderbook_archive_dir: str | Path) -> list[Dict[str, Any]]:
    return load_jsonl(Path(orderbook_archive_dir) / "orderbook_snapshots.jsonl")


def _overlap_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "matched_closed_archived_token_count": report.get("matched_closed_archived_token_count"),
        "pending_closed_backfill_due_token_count": report.get("pending_closed_backfill_due_token_count"),
        "closed_backfill_due_token_count": report.get("closed_backfill_due_token_count"),
        "pending_awaiting_observation_window_end_token_count": report.get(
            "pending_awaiting_observation_window_end_token_count"
        ),
        "pending_awaiting_settlement_due_time_token_count": report.get(
            "pending_awaiting_settlement_due_time_token_count"
        ),
        "wrong_due_prevented_count": report.get("wrong_due_prevented_count"),
    }


def _alpha_conclusion(strict_report: Dict[str, Any], calibration_report: Dict[str, Any]) -> str:
    replay = strict_report.get("replay") if isinstance(strict_report.get("replay"), dict) else {}
    resolved_fill_count = int(strict_report.get("resolved_fill_count") or 0)
    resolved_pnl_cents = replay.get("resolved_pnl_cents")
    probability_score_count = int(calibration_report.get("probability_score_sample_count") or 0)
    resolved_pnl_sample_count = int(calibration_report.get("resolved_pnl_sample_count") or 0)
    if resolved_fill_count <= 0:
        return "inconclusive_waiting_for_resolution_or_backfill"
    try:
        pnl = float(resolved_pnl_cents)
    except (TypeError, ValueError):
        return "inconclusive_waiting_for_resolution_or_backfill"
    if pnl < 0.0:
        return "alpha_failed_negative_resolved_pnl"
    if pnl > 0.0 and probability_score_count <= 0:
        return "pnl_positive_but_uncalibrated"
    if (
        pnl > 0.0
        and probability_score_count > 0
        and resolved_pnl_sample_count > 0
        and calibration_report.get("hard_conclusion") == "settlement_calibration_ready_diagnostic_only"
    ):
        return "alpha_candidate_paper_only_review"
    if pnl > 0.0:
        return "pnl_positive_but_uncalibrated"
    return "inconclusive_zero_resolved_pnl"


def _readiness_compact(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "paper_only": report.get("paper_only"),
        "live_gate": report.get("live_gate"),
        "evidence_gate_passed": report.get("evidence_gate_passed"),
        "live_order_path_available": report.get("live_order_path_available"),
        "hard_gate_failures": report.get("hard_gate_failures"),
        "hard_conclusion": report.get("hard_conclusion"),
    }


def build_due_evidence_pipeline_report(
    *,
    paper_journal_dir: str | Path = DEFAULT_PAPER_JOURNAL_DIR,
    orderbook_archive_dir: str | Path = DEFAULT_ORDERBOOK_ARCHIVE_DIR,
    backfill_dir: str | Path = DEFAULT_BACKFILL_DIR,
    generated_at: Optional[str] = None,
    replay_time: Optional[str] = None,
    execute_closed_backfill: bool = False,
    confirm: Optional[str] = None,
    size: float = 1.0,
    max_samples: int = 1000,
) -> Dict[str, Any]:
    replay_time = replay_time or generated_at
    due_dry_run = build_due_refresh_report(
        backfill_dir=backfill_dir,
        orderbook_archive_dir=orderbook_archive_dir,
        generated_at=generated_at,
        replay_time=replay_time,
        size=size,
        max_samples=max_samples,
        execute=False,
    )
    plan_report = due_dry_run.get("plan_report") if isinstance(due_dry_run.get("plan_report"), dict) else {}
    followup = (
        plan_report.get("closed_backfill_followup_plan")
        if isinstance(plan_report.get("closed_backfill_followup_plan"), dict)
        else {}
    )
    due_count = int(plan_report.get("closed_backfill_due_token_count") or 0)
    due_status = "closed_backfill_due" if due_count > 0 else "waiting_for_settlement_due_time"
    if int(plan_report.get("pending_awaiting_observation_window_end_token_count") or 0) > 0:
        due_status = "waiting_for_observation_window_end"

    due_execute = None
    if execute_closed_backfill:
        due_execute = build_due_refresh_report(
            backfill_dir=backfill_dir,
            orderbook_archive_dir=orderbook_archive_dir,
            generated_at=generated_at,
            replay_time=replay_time,
            size=size,
            max_samples=max_samples,
            execute=True,
            confirm=confirm,
        )
        execution = due_execute.get("execution") if isinstance(due_execute.get("execution"), dict) else {}
        due_status = str(execution.get("status") or due_status)

    effective_due_report = due_execute if isinstance(due_execute, dict) else due_dry_run
    after_plan = effective_due_report.get("after_plan_report")
    if not isinstance(after_plan, dict):
        after_plan = plan_report

    queue_dir = default_strict_gate_queue_dir(paper_journal_dir)
    strict_report = build_strict_gate_replay_report_from_dirs(
        queue_dir=queue_dir,
        orderbook_archive_dir=orderbook_archive_dir,
        journal_dir=paper_journal_dir,
        backfill_dir=backfill_dir,
        replay_time=replay_time,
        size=size,
    )
    orderbook_rows = _load_orderbooks(orderbook_archive_dir)
    backfill_rows = load_closed_backfill_records_with_snapshot_supplements(backfill_dir)
    overlap_rows = _closed_records_with_archived_overlap(backfill_rows, orderbook_rows)
    official_value_report = build_official_value_backfill_report(
        overlap_rows,
        fetch_external=False,
        max_gap_samples=20,
        max_backfill_plan_requests=100,
    )
    ready_supplements = [
        row
        for row in official_value_report.get("supplements") or []
        if isinstance(row, dict) and row.get("status") == "ready"
    ]
    backfill_rows_with_official = apply_official_value_supplements(
        backfill_rows,
        supplements=ready_supplements,
    )
    truth_audit_report = build_settlement_truth_audit_report(backfill_rows_with_official)
    historical_evidence_report = historical_evidence_from_replay_report(
        strict_report,
        source="due_evidence_pipeline_strict_gate_replay",
    )
    calibration_report = build_settlement_calibration_report(
        backfill_rows_with_official,
        historical_evidence_supplements=historical_evidence_report.get("supplements") or [],
    )
    live_bundle_report = build_live_evidence_bundle_report(
        queue_records=load_jsonl(queue_dir / "strict_gate_queue.jsonl"),
        orderbook_snapshots=orderbook_rows,
        closed_backfill_records=backfill_rows,
        audit_records=load_jsonl(Path(paper_journal_dir) / "resolved_audits.jsonl"),
        replay_time=replay_time,
        size=size,
        generated_at=generated_at,
        official_value_supplements=ready_supplements,
    )
    readiness_report = build_live_readiness_report(
        journal_dir=paper_journal_dir,
        backfill_dir=backfill_dir,
        strict_gate_queue_dir=queue_dir,
        strict_gate_replay_report=strict_report,
        settlement_calibration_report=calibration_report,
        orderbook_archive_coverage_report=after_plan,
        live_permission=False,
        live_order_path_available=False,
        generated_at=generated_at,
    )
    replay = strict_report.get("replay") if isinstance(strict_report.get("replay"), dict) else {}
    alpha_conclusion = _alpha_conclusion(strict_report, calibration_report)
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_only": True,
        "diagnostic_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "generated_at": generated_at or plan_report.get("generated_at"),
        "replay_time": replay_time,
        "paper_journal_dir": str(paper_journal_dir),
        "orderbook_archive_dir": str(orderbook_archive_dir),
        "backfill_dir": str(backfill_dir),
        "due_status": due_status,
        "closed_backfill_executed": (
            (due_execute or {}).get("execution", {}).get("status") == "executed"
            if isinstance((due_execute or {}).get("execution"), dict)
            else False
        ),
        "next_execution_time_utc": followup.get("earliest_settlement_due_time")
        or followup.get("next_settlement_due_check_after"),
        "before_token_overlap": _overlap_summary(plan_report),
        "after_token_overlap": _overlap_summary(after_plan),
        "strict_replay": {
            "fill_count": replay.get("fill_count"),
            "resolved_fill_count": strict_report.get("resolved_fill_count"),
            "missing_resolution_count": replay.get("missing_resolution_count"),
            "missed_fill_count": replay.get("missed_fill_count"),
            "resolved_pnl_cents": replay.get("resolved_pnl_cents"),
            "brier_score": replay.get("brier_score"),
            "log_loss": replay.get("log_loss"),
            "by_strategy_bucket": strict_report.get("by_strategy_bucket") or [],
            "by_price_bucket": strict_report.get("by_price_bucket") or [],
        },
        "official_truth": {
            "target_scope": "closed_archive_overlap",
            "archived_yes_token_count": len(_archived_yes_token_ids(orderbook_rows)),
            "overlap_record_count": len(overlap_rows),
            "ready_count": official_value_report.get("ready_count"),
            "gap_count": official_value_report.get("gap_count"),
            "request_plan": official_value_report.get("official_observation_backfill_plan"),
        },
        "settlement_truth_audit": {
            "hard_conclusion": truth_audit_report.get("hard_conclusion"),
            "audited_with_official_count": truth_audit_report.get("audited_with_official_count"),
            "mismatch_count": truth_audit_report.get("mismatch_count"),
            "gap_count": truth_audit_report.get("gap_count"),
        },
        "settlement_calibration": {
            "hard_conclusion": calibration_report.get("hard_conclusion"),
            "official_truth_sample_count": calibration_report.get("official_truth_sample_count"),
            "probability_score_sample_count": calibration_report.get("probability_score_sample_count"),
            "resolved_pnl_sample_count": calibration_report.get("resolved_pnl_sample_count"),
            "historical_evidence_no_lookahead": calibration_report.get("historical_evidence_no_lookahead"),
        },
        "alpha_conclusion": alpha_conclusion,
        "readiness_report_compact": _readiness_compact(readiness_report),
        "component_reports": {
            "due_dry_run": due_dry_run,
            "due_execute": due_execute,
            "strict_gate_replay": strict_report,
            "official_value_backfill": official_value_report,
            "settlement_truth_audit": truth_audit_report,
            "historical_evidence": historical_evidence_report,
            "settlement_calibration": calibration_report,
            "live_evidence_bundle": live_bundle_report,
            "readiness": readiness_report,
        },
        "hard_conclusion": "paper_only_" + alpha_conclusion,
    }


def _write_component_artifacts(summary_output: Path, report: Dict[str, Any]) -> Dict[str, str]:
    out_dir = summary_output.parent
    components = report.get("component_reports") if isinstance(report.get("component_reports"), dict) else {}
    paths = {
        "strict_gate_replay": out_dir / "strict_gate_replay_after_settlement_due.json",
        "official_value_overlap": out_dir / "official_value_overlap_after_settlement_due.jsonl",
        "settlement_calibration": out_dir / "settlement_calibration_after_settlement_due.json",
        "live_evidence_bundle": out_dir / "live_evidence_bundle_after_settlement_due.json",
        "readiness": out_dir / "readiness_after_settlement_due.json",
    }
    if isinstance(components.get("strict_gate_replay"), dict):
        _write_json(paths["strict_gate_replay"], components["strict_gate_replay"])
    official = components.get("official_value_backfill") if isinstance(components.get("official_value_backfill"), dict) else {}
    _write_jsonl(paths["official_value_overlap"], official.get("supplements") or [])
    if isinstance(components.get("settlement_calibration"), dict):
        _write_json(paths["settlement_calibration"], components["settlement_calibration"])
    if isinstance(components.get("live_evidence_bundle"), dict):
        _write_json(paths["live_evidence_bundle"], components["live_evidence_bundle"])
    if isinstance(components.get("readiness"), dict):
        _write_json(paths["readiness"], components["readiness"])
    return {key: str(path) for key, path in paths.items() if path.exists()}


def _summary_for_output(report: Dict[str, Any]) -> Dict[str, Any]:
    summary = dict(report)
    summary.pop("component_reports", None)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paper-only due evidence pipeline from archived orderbooks through calibration/readiness inputs.",
    )
    parser.add_argument("--paper-journal-dir", default=str(DEFAULT_PAPER_JOURNAL_DIR))
    parser.add_argument("--orderbook-archive-dir", default=str(DEFAULT_ORDERBOOK_ARCHIVE_DIR))
    parser.add_argument("--backfill-dir", default=str(DEFAULT_BACKFILL_DIR))
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--replay-time", default=None)
    parser.add_argument("--size", type=float, default=1.0)
    parser.add_argument("--max-samples", type=int, default=1000)
    parser.add_argument("--execute-closed-backfill", action="store_true")
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--summary-output", default=None)
    parser.add_argument(
        "--include-component-reports",
        action="store_true",
        help="Print full nested component reports to stdout. Summary output files always stay compact.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    report = build_due_evidence_pipeline_report(
        paper_journal_dir=args.paper_journal_dir,
        orderbook_archive_dir=args.orderbook_archive_dir,
        backfill_dir=args.backfill_dir,
        generated_at=args.generated_at,
        replay_time=args.replay_time,
        execute_closed_backfill=bool(args.execute_closed_backfill),
        confirm=args.confirm,
        size=float(args.size),
        max_samples=max(0, int(args.max_samples)),
    )
    output_report = report if args.include_component_reports else _summary_for_output(report)
    if args.summary_output:
        summary_path = Path(args.summary_output)
        artifact_paths = (
            _write_component_artifacts(summary_path, report)
            if report.get("closed_backfill_executed") is True
            else {}
        )
        output_report = dict(output_report)
        output_report["artifact_paths"] = artifact_paths
        _write_json(summary_path, output_report)
    print(json.dumps(output_report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
