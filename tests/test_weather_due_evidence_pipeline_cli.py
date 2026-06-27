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


def _station_archive_row(station_code, source, city, *, token_id, market_slug, timezone="UTC+03:00"):
    return _archive_row(
        market_id=f"market-{token_id}",
        market_slug=market_slug,
        token_id=token_id,
        city=city,
        target_date="2026-06-27",
        settlement_station_code=station_code,
        settlement_source=source,
        settlement_timezone=timezone,
        market_close_time="2026-06-27T12:00:00Z",
        end_time="2026-06-27T12:00:00Z",
        settlement_spec={
            "target_date": "2026-06-27",
            "timezone": timezone,
            "market_close_time": "2026-06-27T12:00:00Z",
            "end_time": "2026-06-27T12:00:00Z",
            "station_code": station_code,
            "settlement_source": source,
        },
    )


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
        "build_strict_gate_replay_report",
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


def test_due_evidence_pipeline_groups_current_archive_by_due_time_source_and_station(tmp_path):
    archive_dir = tmp_path / "archive"
    backfill_dir = tmp_path / "backfill"
    paper_dir = tmp_path / "paper"
    _append_jsonl(
        archive_dir / "orderbook_snapshots.jsonl",
        [
            _station_archive_row("LTAC", "metar", "ankara", token_id="ltac-token", market_slug="ankara-slug"),
            _station_archive_row("UUWW", "metar", "moscow", token_id="uwww-token", market_slug="moscow-slug"),
            _station_archive_row("EGLC", "metar", "london", token_id="eglc-token", market_slug="london-slug", timezone="UTC+00:00"),
            _station_archive_row("LTFM", "noaa", "istanbul", token_id="ltfm-token", market_slug="istanbul-slug"),
        ],
    )

    report = pipeline_cli.build_due_evidence_pipeline_report(
        paper_journal_dir=paper_dir,
        orderbook_archive_dir=archive_dir,
        backfill_dir=backfill_dir,
        generated_at="2026-06-27T15:35:32Z",
        replay_time="2026-06-27T15:35:32Z",
    )

    groups = {row["group_id"]: row for row in report["execution_groups"]}
    assert groups["due_at_2026_06_28T025959Z_supported_metar"]["station_codes"] == ["LTAC", "UUWW"]
    assert groups["due_at_2026_06_28T025959Z_supported_metar"]["token_count"] == 2
    assert groups["due_at_2026_06_28T055959Z_supported_metar"]["station_codes"] == ["EGLC"]
    assert groups["unsupported_noaa_adapter"]["station_codes"] == ["LTFM"]
    assert groups["unsupported_noaa_adapter"]["supported_official_source"] is False
    assert groups["unsupported_noaa_adapter"]["next_action"] == "skip_until_official_source_adapter_exists"


def test_due_evidence_pipeline_executes_only_included_station_subset(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    backfill_dir = tmp_path / "backfill"
    paper_dir = tmp_path / "paper"
    _append_jsonl(
        archive_dir / "orderbook_snapshots.jsonl",
        [
            _station_archive_row("LTAC", "metar", "ankara", token_id="ltac-token", market_slug="ankara-slug"),
            _station_archive_row("UUWW", "metar", "moscow", token_id="uwww-token", market_slug="moscow-slug"),
            _station_archive_row("EGLC", "metar", "london", token_id="eglc-token", market_slug="london-slug", timezone="UTC+00:00"),
            _station_archive_row("LTFM", "noaa", "istanbul", token_id="ltfm-token", market_slug="istanbul-slug"),
        ],
    )
    calls = {}

    def fake_targeted(**kwargs):
        calls["market_slugs"] = list(kwargs["market_slugs"])
        return {
            "payload_status": "no_targeted_closed_weather_markets",
            "payload_diagnostics": {
                "closed_market_slug_count": 0,
                "open_market_slug_count": 2,
            },
        }

    monkeypatch.setattr(pipeline_cli, "run_targeted_closed_weather_backfill_from_market_slugs", fake_targeted)

    report = pipeline_cli.build_due_evidence_pipeline_report(
        paper_journal_dir=paper_dir,
        orderbook_archive_dir=archive_dir,
        backfill_dir=backfill_dir,
        generated_at="2026-06-28T03:05:00Z",
        replay_time="2026-06-28T03:05:00Z",
        execute_closed_backfill=True,
        confirm="PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH",
        include_settlement_sources=["metar"],
        include_station_codes=["LTAC", "UUWW"],
    )

    assert calls["market_slugs"] == ["ankara-slug", "moscow-slug"]
    assert report["closed_backfill_executed"] is True
    assert report["filtered_execution_groups"][0]["station_codes"] == ["LTAC", "UUWW"]
    assert report["filters"]["include_station_code"] == ["LTAC", "UUWW"]


def test_due_evidence_pipeline_reports_official_truth_ready_market_unresolved(monkeypatch, tmp_path):
    def fake_due_refresh_report(**kwargs):
        return {
            "plan_report": {
                "generated_at": "2026-06-28T03:05:00Z",
                "closed_backfill_due_token_count": 1,
                "closed_backfill_followup_plan": {
                    "earliest_settlement_due_time": "2026-06-28T02:59:59Z",
                    "requests": [
                        {
                            "market_slug": "ankara-slug",
                            "token_id": "ltac-token",
                            "settlement_station_code": "LTAC",
                            "settlement_source": "metar",
                            "settlement_due_time": "2026-06-28T02:59:59Z",
                            "target_date": "2026-06-27",
                            "settlement_spec": {
                                "station_code": "LTAC",
                                "settlement_source": "metar",
                                "target_date": "2026-06-27",
                            },
                        }
                    ],
                },
            }
        }

    monkeypatch.setattr(pipeline_cli, "build_due_refresh_report", fake_due_refresh_report)
    monkeypatch.setattr(
        pipeline_cli,
        "run_targeted_closed_weather_backfill_from_market_slugs",
        lambda **kwargs: {
            "payload_status": "no_targeted_closed_weather_markets",
            "payload_diagnostics": {
                "closed_market_slug_count": 0,
                "open_market_slug_count": 1,
            },
        },
    )
    monkeypatch.setattr(pipeline_cli, "_load_orderbooks", lambda path: [])
    monkeypatch.setattr(pipeline_cli, "load_closed_backfill_records_with_snapshot_supplements", lambda path: [])
    monkeypatch.setattr(
        pipeline_cli,
        "build_strict_gate_replay_report",
        lambda **kwargs: {"replay": {"fill_count": 0, "resolved_pnl_cents": None}, "resolved_fill_count": 0},
    )
    monkeypatch.setattr(
        pipeline_cli,
        "build_official_value_backfill_report",
        lambda *args, **kwargs: {
            "ready_count": 1,
            "gap_count": 0,
            "supplements": [
                {
                    "status": "ready",
                    "market_slug": "ankara-slug",
                    "official_final_value": 27.0,
                }
            ],
            "official_observation_backfill_plan": {"request_count": 1},
        },
    )
    monkeypatch.setattr(pipeline_cli, "apply_official_value_supplements", lambda records, supplements: list(records))
    monkeypatch.setattr(
        pipeline_cli,
        "build_settlement_truth_audit_report",
        lambda records: {"hard_conclusion": "settlement_truth_audit_no_records"},
    )
    monkeypatch.setattr(pipeline_cli, "historical_evidence_from_replay_report", lambda report, source: {"supplements": []})
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
    monkeypatch.setattr(pipeline_cli, "build_live_evidence_bundle_report", lambda **kwargs: {})
    monkeypatch.setattr(pipeline_cli, "build_live_readiness_report", lambda **kwargs: {})

    report = pipeline_cli.build_due_evidence_pipeline_report(
        paper_journal_dir=tmp_path / "paper",
        orderbook_archive_dir=tmp_path / "archive",
        backfill_dir=tmp_path / "backfill",
        generated_at="2026-06-28T03:05:00Z",
        replay_time="2026-06-28T03:05:00Z",
        execute_closed_backfill=True,
        confirm="PAPER_ONLY_ARCHIVED_ORDERBOOK_REFRESH",
        fetch_external_official_values=True,
    )

    assert report["alpha_conclusion"] == "official_truth_ready_market_unresolved"
    assert report["resolved_pnl_unavailable_reason"] == "polymarket_market_not_resolved"
    assert report["next_polymarket_resolution_check_after"] == "2026-06-28T04:05:00Z"
    assert report["unresolved_tokens"] == [
        {
            "market_slug": "ankara-slug",
            "settlement_due_time": "2026-06-28T02:59:59Z",
            "settlement_source": "metar",
            "station_code": "LTAC",
            "target_date": "2026-06-27",
            "token_id": "ltac-token",
        }
    ]
    assert report["official_truth"]["official_truth_sample_count"] == 1
    assert report["targeted_closed_backfill"]["open_market_slug_count"] == 1
