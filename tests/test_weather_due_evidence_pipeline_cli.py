from __future__ import annotations

import json

from scripts import weather_run_due_evidence_pipeline as pipeline_cli
from src.trading.weather_paper_journal import _append_jsonl


def _archive_row(**overrides):
    row = {
        "recorded_at": "2026-06-27T11:55:00Z",
        "market_id": "market-ankara",
        "market_slug": "highest-temperature-in-ankara-on-june-27-2026-24corbelow",
        "token_id": "ankara-token",
        "side": "yes",
        "city": "ankara",
        "target_date": "2026-06-27",
        "market_close_time": "2026-06-27T12:00:00Z",
        "end_time": "2026-06-27T12:00:00Z",
        "settlement_station_code": "LTAC",
        "settlement_source": "metar",
        "settlement_timezone": "UTC+03:00",
        "settlement_spec": {
            "target_date": "2026-06-27",
            "timezone": "UTC+03:00",
            "market_close_time": "2026-06-27T12:00:00Z",
            "end_time": "2026-06-27T12:00:00Z",
            "station_code": "LTAC",
            "settlement_source": "metar",
        },
        "ask_ladder": [{"price": 0.002, "size": 2.0}],
        "bid_ladder": [{"price": 0.001, "size": 2.0}],
    }
    row.update(overrides)
    return row


def test_due_evidence_pipeline_cli_blocks_execute_before_settlement_due(tmp_path, capsys):
    paper_dir = tmp_path / "paper"
    archive_dir = tmp_path / "archive"
    backfill_dir = tmp_path / "backfill"
    summary_path = tmp_path / "due_pipeline.json"
    _append_jsonl(archive_dir / "orderbook_snapshots.jsonl", [_archive_row()])

    pipeline_cli.main(
        [
            "--paper-journal-dir",
            str(paper_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--backfill-dir",
            str(backfill_dir),
            "--generated-at",
            "2026-06-27T12:05:00Z",
            "--replay-time",
            "2026-06-27T12:05:00Z",
            "--execute-closed-backfill",
            "--confirm",
            "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH",
            "--summary-output",
            str(summary_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    written = json.loads(summary_path.read_text(encoding="utf-8"))
    assert output["due_status"] == "blocked_until_settlement_due_time"
    assert output["closed_backfill_executed"] is False
    assert output["next_execution_time_utc"] == "2026-06-28T02:59:59Z"
    assert output["before_token_overlap"]["closed_backfill_due_token_count"] == 0
    assert output["before_token_overlap"]["wrong_due_prevented_count"] == 1
    assert output["alpha_conclusion"] == "inconclusive_waiting_for_resolution_or_backfill"
    assert written["due_status"] == output["due_status"]
    assert written["artifact_paths"] == {}


def test_due_evidence_pipeline_attempts_execute_when_due_count_positive(monkeypatch, tmp_path):
    calls = []

    def fake_due_refresh_report(**kwargs):
        calls.append(kwargs)
        if kwargs.get("execute"):
            return {
                "execution": {"status": "executed"},
                "plan_report": {"closed_backfill_due_token_count": 1},
                "after_plan_report": {
                    "matched_closed_archived_token_count": 1,
                    "closed_backfill_due_token_count": 0,
                },
            }
        return {
            "plan_report": {
                "generated_at": "2026-06-28T03:05:00Z",
                "closed_backfill_due_token_count": 1,
                "closed_backfill_followup_plan": {
                    "earliest_settlement_due_time": "2026-06-28T02:59:59Z",
                },
            }
        }

    monkeypatch.setattr(pipeline_cli, "build_due_refresh_report", fake_due_refresh_report)
    monkeypatch.setattr(
        pipeline_cli,
        "build_strict_gate_replay_report_from_dirs",
        lambda **kwargs: {
            "replay": {
                "fill_count": 1,
                "missing_resolution_count": 0,
                "missed_fill_count": 0,
                "resolved_pnl_cents": 10.0,
                "brier_score": 0.1,
                "log_loss": 0.2,
            },
            "resolved_fill_count": 1,
            "by_strategy_bucket": [],
            "by_price_bucket": [],
        },
    )
    monkeypatch.setattr(pipeline_cli, "_load_orderbooks", lambda path: [])
    monkeypatch.setattr(pipeline_cli, "load_closed_backfill_records_with_snapshot_supplements", lambda path: [])
    monkeypatch.setattr(pipeline_cli, "_closed_records_with_archived_overlap", lambda records, books: [])
    monkeypatch.setattr(pipeline_cli, "_archived_yes_token_ids", lambda books: set())
    monkeypatch.setattr(
        pipeline_cli,
        "build_official_value_backfill_report",
        lambda *args, **kwargs: {"supplements": [], "ready_count": 0, "gap_count": 0},
    )
    monkeypatch.setattr(pipeline_cli, "apply_official_value_supplements", lambda records, supplements: list(records))
    monkeypatch.setattr(
        pipeline_cli,
        "build_settlement_truth_audit_report",
        lambda records: {"hard_conclusion": "settlement_truth_audit_no_records"},
    )
    monkeypatch.setattr(
        pipeline_cli,
        "historical_evidence_from_replay_report",
        lambda report, source: {"supplements": []},
    )
    monkeypatch.setattr(
        pipeline_cli,
        "build_settlement_calibration_report",
        lambda *args, **kwargs: {
            "hard_conclusion": "settlement_calibration_no_resolved_records",
            "official_truth_sample_count": 0,
            "probability_score_sample_count": 0,
            "resolved_pnl_sample_count": 0,
        },
    )
    monkeypatch.setattr(
        pipeline_cli,
        "build_live_evidence_bundle_report",
        lambda **kwargs: {"hard_conclusion": "live_evidence_bundle_needs_more_evidence"},
    )
    monkeypatch.setattr(
        pipeline_cli,
        "build_live_readiness_report",
        lambda **kwargs: {"live_gate": False, "live_order_path_available": False},
    )

    report = pipeline_cli.build_due_evidence_pipeline_report(
        paper_journal_dir=tmp_path / "paper",
        orderbook_archive_dir=tmp_path / "archive",
        backfill_dir=tmp_path / "backfill",
        generated_at="2026-06-28T03:05:00Z",
        replay_time="2026-06-28T03:05:00Z",
        execute_closed_backfill=True,
        confirm="PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH",
    )

    assert calls[0]["execute"] is False
    assert calls[1]["execute"] is True
    assert report["closed_backfill_executed"] is True
    assert report["after_token_overlap"]["matched_closed_archived_token_count"] == 1
