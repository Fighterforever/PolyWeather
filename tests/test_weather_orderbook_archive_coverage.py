from __future__ import annotations

import json

from scripts import weather_orderbook_closed_backfill_plan_report as plan_cli
from scripts import weather_archived_orderbook_due_refresh_report as refresh_cli
from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_orderbook_archive_coverage import (
    build_orderbook_archive_coverage_report,
    build_orderbook_closed_token_coverage_report,
)


def _payload_row(**overrides):
    row = {
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-28-2026-30c-or-above",
        "city": "seoul",
        "bucket_label": ">= 30°C",
        "token_id": "yes-token",
        "outcome": "Yes",
        "side": "yes",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "tradable": True,
        "end_date": "2026-06-28T12:00:00Z",
        "order_book": {
            "best_ask": 0.64,
            "best_bid": 0.60,
            "spread": 0.04,
            "asks": [{"price": 0.64, "size": 2.0}],
            "bids": [{"price": 0.60, "size": 2.0}],
        },
    }
    row.update(overrides)
    return row


def _archive_row(**overrides):
    row = {
        "recorded_at": "2026-06-28T10:00:00Z",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-28-2026-30c-or-above",
        "token_id": "yes-token",
        "side": "yes",
        "target_date": "2026-06-28",
        "end_time": "2026-06-28T12:00:00Z",
        "settlement_station_code": "RKSI",
        "settlement_source": "metar",
        "bucket_type": "ge",
        "best_ask": 0.64,
        "best_bid": 0.60,
        "ask_ladder": [{"price": 0.64, "size": 2.0}],
        "bid_ladder": [{"price": 0.60, "size": 2.0}],
    }
    row.update(overrides)
    return row


def _closed_record(**overrides):
    row = {
        "status": "resolved",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-28-2026-30c-or-above",
        "city": "seoul",
        "bucket_label": ">= 30°C",
        "target_date": "2026-06-28",
        "end_date": "2026-06-28T12:00:00Z",
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "winning_outcome": "Yes",
        "winning_token_id": "yes-token",
        "settlement_spec": {
            "end_time": "2026-06-28T12:00:00Z",
            "station_code": "RKSI",
            "settlement_source": "metar",
        },
    }
    row.update(overrides)
    return row


def test_orderbook_archive_coverage_ready_when_active_yes_token_is_archived():
    report = build_orderbook_archive_coverage_report(
        {"snapshot_id": "payload-1", "rows": [_payload_row()]},
        archive_rows=[_archive_row()],
        generated_at="2026-06-28T10:05:00Z",
    )

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["hard_conclusion"] == "orderbook_archive_coverage_ready"
    assert report["eligible_preresolution_count"] == 1
    assert report["covered_preresolution_count"] == 1
    assert report["coverage_rate"] == 1.0
    assert report["covered_samples"][0]["latest_archive_at"] == "2026-06-28T10:00:00Z"


def test_orderbook_archive_coverage_reports_missing_preresolution_archive():
    report = build_orderbook_archive_coverage_report(
        {"rows": [_payload_row()]},
        archive_rows=[],
        generated_at="2026-06-28T10:05:00Z",
    )

    assert report["hard_conclusion"] == "orderbook_archive_coverage_incomplete"
    assert report["eligible_preresolution_count"] == 1
    assert report["covered_preresolution_count"] == 0
    assert report["gaps_by_reason"] == [{"reason": "missing_preresolution_archive", "count": 1}]


def test_orderbook_archive_coverage_excludes_ended_or_non_yes_rows():
    report = build_orderbook_archive_coverage_report(
        {
            "rows": [
                _payload_row(token_id="yes-ended", end_date="2026-06-28T09:00:00Z"),
                _payload_row(token_id="no-token", side="no", outcome="No"),
            ]
        },
        archive_rows=[],
        generated_at="2026-06-28T10:05:00Z",
    )

    assert report["hard_conclusion"] == "orderbook_archive_coverage_no_active_eligible_markets"
    assert report["active_yes_token_count"] == 1
    assert report["eligible_preresolution_count"] == 0
    assert report["gaps_by_reason"] == [{"reason": "market_already_ended", "count": 1}]


def test_orderbook_closed_token_coverage_ready_when_archived_token_matches_closed_yes_token():
    report = build_orderbook_closed_token_coverage_report(
        closed_records=[_closed_record()],
        orderbook_snapshots=[_archive_row()],
        generated_at="2026-06-28T10:05:00Z",
    )

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["hard_conclusion"] == "orderbook_closed_token_coverage_ready"
    assert report["closed_yes_token_count"] == 1
    assert report["archived_unique_token_count"] == 1
    assert report["matched_closed_archived_token_count"] == 1
    assert report["unmatched_closed_token_count"] == 0
    assert report["unmatched_archived_token_count"] == 0
    assert report["matched_samples"][0]["token_id"] == "yes-token"


def test_orderbook_closed_token_coverage_reports_archived_tokens_waiting_for_closed_backfill():
    report = build_orderbook_closed_token_coverage_report(
        closed_records=[],
        orderbook_snapshots=[_archive_row(token_id="active-token")],
        generated_at="2026-06-28T10:05:00Z",
    )

    assert report["hard_conclusion"] == "orderbook_closed_token_coverage_no_closed_yes_tokens"
    assert report["closed_yes_token_count"] == 0
    assert report["archived_unique_token_count"] == 1
    assert report["unmatched_archived_token_count"] == 1
    assert report["pending_closed_backfill_await_market_end_token_count"] == 1
    assert report["closed_backfill_followup_plan"]["next_action"] == "wait_for_archived_markets_to_reach_end_time"
    assert report["closed_backfill_followup_plan"]["next_await_market_end"] == "2026-06-28T12:00:00Z"
    assert report["closed_backfill_followup_plan"]["next_refresh_check_after"] == "2026-06-28T12:00:00Z"
    assert report["closed_backfill_followup_plan"]["next_await_market_end_token_count"] == 1
    assert report["closed_backfill_followup_plan"]["next_await_market_query_count"] == 1
    assert report["closed_backfill_followup_plan"]["next_await_market_queries"] == [
        "highest-temperature-in-seoul-on-june-28-2026-30c-or-above"
    ]
    assert report["gaps_by_reason"] == [
        {"reason": "archived_orderbook_waiting_for_market_end", "count": 1}
    ]
    assert report["archived_markets_pending_closed_backfill_samples"][0]["token_id"] == "active-token"
    assert report["archived_markets_pending_closed_backfill_samples"][0]["pending_closed_backfill_status"] == "await_market_end"


def test_orderbook_closed_token_coverage_reports_due_closed_backfill_refresh():
    report = build_orderbook_closed_token_coverage_report(
        closed_records=[],
        orderbook_snapshots=[_archive_row(token_id="ended-token")],
        generated_at="2026-06-28T13:05:00Z",
    )

    assert report["pending_closed_backfill_due_token_count"] == 1
    assert report["closed_backfill_followup_plan"]["request_count"] == 1
    assert report["closed_backfill_followup_plan"]["next_action"] == (
        "refresh_closed_weather_backfill_for_due_archived_markets"
    )
    assert report["closed_backfill_followup_plan"]["next_refresh_check_after"] == "2026-06-28T13:05:00Z"
    assert report["closed_backfill_followup_plan"]["next_await_market_end"] is None
    assert report["closed_backfill_followup_plan"]["market_queries"] == [
        "highest-temperature-in-seoul-on-june-28-2026-30c-or-above"
    ]
    assert report["closed_backfill_followup_plan"]["recommended_command"][-2:] == [
        "--market-slug",
        "highest-temperature-in-seoul-on-june-28-2026-30c-or-above",
    ]
    assert report["closed_backfill_followup_plan"]["requests"][0]["token_id"] == "ended-token"
    assert report["gaps_by_reason"] == [
        {"reason": "archived_orderbook_pending_closed_backfill_due", "count": 1}
    ]


def test_orderbook_closed_token_coverage_reports_missing_archive_end_time():
    report = build_orderbook_closed_token_coverage_report(
        closed_records=[],
        orderbook_snapshots=[_archive_row(token_id="legacy-token", end_time=None, target_date=None)],
        generated_at="2026-06-28T13:05:00Z",
    )

    assert report["pending_closed_backfill_missing_end_time_token_count"] == 1
    assert report["closed_backfill_followup_plan"]["next_action"] == "preserve_end_time_in_future_orderbook_archives"
    assert report["closed_backfill_followup_plan"]["next_refresh_check_after"] is None
    assert report["gaps_by_reason"] == [
        {"reason": "archived_orderbook_pending_closed_backfill_missing_end_time", "count": 1}
    ]


def test_orderbook_closed_token_coverage_reports_closed_markets_missing_archive():
    report = build_orderbook_closed_token_coverage_report(
        closed_records=[_closed_record(token_id_by_outcome={"Yes": "closed-token", "No": "no-token"})],
        orderbook_snapshots=[],
        generated_at="2026-06-28T10:05:00Z",
    )

    assert report["hard_conclusion"] == "orderbook_closed_token_coverage_no_archived_tokens"
    assert report["closed_yes_token_count"] == 1
    assert report["archived_unique_token_count"] == 0
    assert report["unmatched_closed_token_count"] == 1
    assert report["gaps_by_reason"] == [
        {"reason": "closed_market_missing_archived_orderbook", "count": 1}
    ]
    assert report["closed_markets_missing_archive_samples"][0]["token_id"] == "closed-token"


def test_orderbook_closed_backfill_plan_cli_reports_due_query_without_row_samples(tmp_path, capsys):
    archive_dir = tmp_path / "orderbooks"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        archive_dir / "orderbook_snapshots.jsonl",
        [_archive_row(token_id="ended-token")],
    )

    plan_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--generated-at",
            "2026-06-28T13:05:00Z",
            "--summary-only",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert report["pending_closed_backfill_due_token_count"] == 1
    assert report["closed_backfill_followup_plan"]["market_query_count"] == 1
    assert report["closed_backfill_followup_plan"]["market_queries"] == [
        "highest-temperature-in-seoul-on-june-28-2026-30c-or-above"
    ]
    assert "requests" not in report["closed_backfill_followup_plan"]
    assert "next_await_market_queries" not in report["closed_backfill_followup_plan"]
    assert "archived_markets_pending_closed_backfill_samples" not in report


def test_archived_orderbook_due_refresh_cli_dry_run_does_not_write_backfill(tmp_path, capsys):
    archive_dir = tmp_path / "orderbooks"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        archive_dir / "orderbook_snapshots.jsonl",
        [_archive_row(token_id="ended-token")],
    )

    refresh_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--generated-at",
            "2026-06-28T13:05:00Z",
            "--summary-only",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert report["paper_only"] is True
    assert report["execution"]["status"] == "dry_run"
    assert report["execution"]["due_market_query_count"] == 1
    assert not (backfill_dir / "closed_markets.jsonl").exists()


def test_archived_orderbook_due_refresh_cli_executes_with_explicit_confirmation(tmp_path, monkeypatch, capsys):
    archive_dir = tmp_path / "orderbooks"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        archive_dir / "orderbook_snapshots.jsonl",
        [_archive_row(token_id="ended-token")],
    )
    calls = {}

    def fake_backfill(**kwargs):
        calls["backfill"] = kwargs
        return {
            "schema_version": "polyweather_weather_closed_backfill.v1",
            "targeted": True,
            "backfill_journal": {"record_count": 1},
        }

    def fake_replay(**kwargs):
        calls["replay"] = kwargs
        return {
            "schema_version": "polyweather_weather_closed_historical_replay.v1",
            "paper_only": True,
            "diagnostic_only": True,
            "counts_for_live_gate": False,
            "hard_conclusion": "preresolution_orderbook_replay_ready_for_settlement_calibration",
            "input_summary": {"candidate_count": 1},
            "resolved_outcome_count": 1,
            "replay": {
                "candidate_count": 1,
                "fill_count": 1,
                "missed_fill_count": 0,
                "no_visible_orderbook_count": 0,
                "missing_resolution_count": 0,
                "resolved_pnl_usdc": 0.36,
                "mean_resolved_pnl_per_share": 0.36,
                "fill_rate": 1.0,
            },
            "historical_evidence": {"supplement_count": 1, "gap_count": 0, "gaps_by_reason": []},
        }

    monkeypatch.setattr(refresh_cli, "run_targeted_closed_weather_backfill_from_market_slugs", fake_backfill)
    monkeypatch.setattr(refresh_cli, "build_preresolution_orderbook_replay_report_from_dirs", fake_replay)

    refresh_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--generated-at",
            "2026-06-28T13:05:00Z",
            "--execute",
            "--confirm",
            "PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH",
            "--summary-only",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert report["execution"]["status"] == "executed"
    assert calls["backfill"]["market_slugs"] == [
        "highest-temperature-in-seoul-on-june-28-2026-30c-or-above"
    ]
    assert calls["backfill"]["backfill_dir"] == backfill_dir
    assert calls["replay"]["backfill_dir"] == backfill_dir
    assert report["execution"]["preresolution_replay_summary"]["replay"]["resolved_pnl_usdc"] == 0.36


def test_archived_orderbook_due_refresh_blocks_execute_without_confirmation(tmp_path, monkeypatch, capsys):
    archive_dir = tmp_path / "orderbooks"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        archive_dir / "orderbook_snapshots.jsonl",
        [_archive_row(token_id="ended-token")],
    )
    called = {"backfill": False}

    def fake_backfill(**kwargs):
        called["backfill"] = True
        return {}

    monkeypatch.setattr(refresh_cli, "run_targeted_closed_weather_backfill_from_market_slugs", fake_backfill)

    refresh_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--generated-at",
            "2026-06-28T13:05:00Z",
            "--execute",
            "--summary-only",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert report["execution"]["status"] == "confirm_missing"
    assert called["backfill"] is False
