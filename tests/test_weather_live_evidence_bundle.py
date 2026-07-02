from __future__ import annotations

import json

from scripts import weather_live_evidence_bundle_report as bundle_cli
from src.trading.weather_live_evidence_bundle import build_live_evidence_bundle_report
from src.trading.weather_paper_journal import _append_jsonl


def _queue_record(**overrides):
    row = {
        "schema_version": "polyweather_weather_strict_gate_queue.v1",
        "queue_record_id": "queue-1",
        "generated_at": "2026-06-26T10:00:00Z",
        "paper_only": True,
        "counts_for_live_gate": False,
        "queue_name": "execution_depth_price",
        "queue_reasons": ["depth_probe"],
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
        "token_id": "yes-token",
        "side": "yes",
        "bucket_type": "ge",
        "strategy_id": "tail_threshold",
        "city": "seoul",
        "q_effective": 0.60,
        "ev_safe": 0.05,
        "p_lcb": 0.75,
        "model_probability": 0.80,
    }
    row.update(overrides)
    return row


def _orderbook(**overrides):
    row = {
        "schema_version": "polyweather_polymarket_orderbook_snapshot.v1",
        "snapshot_id": "book-1",
        "recorded_at": "2026-06-26T10:01:00Z",
        "token_id": "yes-token",
        "ask_ladder": [{"price": 0.62, "size": 2.0}],
        "bid_ladder": [{"price": 0.60, "size": 2.0}],
    }
    row.update(overrides)
    return row


def _archived_orderbook(**overrides):
    row = {
        "schema_version": "polyweather_polymarket_orderbook_snapshot.v1",
        "snapshot_id": "archive-book-1",
        "recorded_at": "2026-06-26T10:00:00Z",
        "source_snapshot_id": "active-scan-1",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
        "event_slug": "seoul-june-26-weather",
        "token_id": "yes-token",
        "side": "yes",
        "target_date": "2026-06-26",
        "end_time": "2026-06-26T12:00:00Z",
        "market_close_time": "2026-06-26T12:00:00Z",
        "observation_window_end_time": "2026-06-26T14:59:59Z",
        "settlement_due_time": "2026-06-26T20:59:59Z",
        "settlement_grace_hours": 6.0,
        "settlement_station_code": "RKSI",
        "settlement_source": "metar",
        "settlement_timezone": "UTC+09:00",
        "settlement_spec": {
            "target_date": "2026-06-26",
            "timezone": "UTC+09:00",
            "market_close_time": "2026-06-26T12:00:00Z",
            "end_time": "2026-06-26T12:00:00Z",
            "observation_window_end_time": "2026-06-26T14:59:59Z",
            "settlement_due_time": "2026-06-26T20:59:59Z",
            "settlement_grace_hours": 6.0,
            "station_code": "RKSI",
            "settlement_source": "metar",
        },
        "bucket_type": "ge",
        "best_ask": 0.64,
        "best_bid": 0.60,
        "spread": 0.04,
        "ask_ladder": [{"price": 0.64, "size": 2.0}],
        "bid_ladder": [{"price": 0.60, "size": 2.0}],
    }
    row.update(overrides)
    return row


def _closed_record(**overrides):
    row = {
        "status": "resolved",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
        "event_slug": "seoul-june-26-weather",
        "city": "seoul",
        "target_date": "2026-06-26",
        "bucket_label": ">= 30°C",
        "parsed_temperature_spec": {
            "city": "seoul",
            "target_date": "2026-06-26",
            "threshold": 30,
            "upper_threshold": None,
            "comparator": "ge",
            "unit": "C",
        },
        "settlement_spec": {
            "station_code": "RKSI",
            "settlement_source": "metar",
            "rounding": "integer_nearest",
            "end_time": "2026-06-26T12:00:00Z",
        },
        "end_date": "2026-06-26T12:00:00Z",
        "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "outcomes": ["Yes", "No"],
        "official_final_value": 30.2,
        "official_final_value_source": "official_test",
    }
    row.update(overrides)
    return row


def _closed_snapshot_row(**overrides):
    row = {
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
        "event_slug": "seoul-june-26-weather",
        "snapshot_recorded_at": "2026-06-26T10:00:00Z",
        "source_snapshot_id": "closed-snapshot-1",
        "closed": False,
        "accepting_orders": True,
        "tradable": True,
        "token_id": "yes-token",
        "outcome": "Yes",
        "side": "yes",
        "price": 0.60,
        "market_probability": 0.62,
        "best_ask": 0.64,
        "best_bid": 0.60,
        "spread": 0.04,
        "end_date": "2026-06-26T12:00:00Z",
        "order_book": {
            "timestamp": "2026-06-26T10:00:00Z",
            "best_ask": 0.64,
            "best_bid": 0.60,
            "spread": 0.04,
            "asks": [{"price": 0.64, "size": 2.0}],
            "bids": [{"price": 0.60, "size": 2.0}],
        },
    }
    row.update(overrides)
    return row


class FakeOfficialObservationRepository:
    def __init__(self, points):
        self.points = points
        self.calls = []

    def load_points(self, *, source_code, station_code, target_date):
        self.calls.append(
            {
                "source_code": source_code,
                "station_code": station_code,
                "target_date": target_date,
            }
        )
        return list(self.points)


def test_live_evidence_bundle_chains_replay_historical_evidence_and_calibration():
    report = build_live_evidence_bundle_report(
        queue_records=[_queue_record()],
        orderbook_snapshots=[_orderbook()],
        closed_backfill_records=[_closed_record()],
        replay_time="2026-06-26T11:00:00Z",
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
        generated_at="2026-06-26T13:00:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_live_evidence_bundle.v1"
    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["hard_conclusion"] == "live_evidence_bundle_ready_for_readiness"
    assert report["blockers"] == []
    assert report["gap_summary"]["primary_blocker"] is None
    assert report["gap_summary"]["gap_count"] == 0
    assert report["gap_summary"]["coverage"]["replay_fill_count"] == 1
    assert report["strict_gate_replay_report"]["hard_conclusion"] == "strict_gate_replay_ready_for_ev_audit"
    assert report["historical_evidence_report"]["supplement_count"] == 1
    assert report["settlement_calibration_report"]["hard_conclusion"] == "settlement_calibration_ready_diagnostic_only"
    assert report["settlement_calibration_report"]["global_calibration"]["mean_resolved_pnl_per_share"] == 0.38


def test_live_evidence_bundle_generates_and_applies_local_official_value_supplements():
    repository = FakeOfficialObservationRepository(
        [
            {"time": "2026-06-26T01:00:00Z", "temp": 28.0},
            {"time": "2026-06-26T05:00:00Z", "temp": 30.2},
        ]
    )

    report = build_live_evidence_bundle_report(
        queue_records=[_queue_record()],
        orderbook_snapshots=[_orderbook()],
        closed_backfill_records=[_closed_record(official_final_value=None)],
        replay_time="2026-06-26T11:00:00Z",
        official_value_repository=repository,
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["official_value_backfill_report"]["ready_count"] == 1
    assert report["official_value_backfill_report"]["gap_count"] == 0
    assert report["official_value_backfill_report"]["target_scope"] == {
        "scope": "closed_archive_overlap",
        "target_record_count": 1,
        "closed_backfill_record_count": 1,
        "archived_yes_token_count": 1,
    }
    assert report["official_value_backfill_report"]["supplements"][0]["official_final_value"] == 30.2
    assert report["settlement_calibration_report"]["official_truth_sample_count"] == 1
    assert report["settlement_calibration_report"]["probability_score_sample_count"] == 1
    assert report["settlement_calibration_report"]["resolved_pnl_sample_count"] == 1
    assert report["settlement_calibration_report"]["calibration_rows"][0]["official_final_value"] == 30.2
    assert report["hard_conclusion"] == "live_evidence_bundle_ready_for_readiness"
    assert repository.calls[0] == {
        "source_code": "metar",
        "station_code": "RKSI",
        "target_date": "2026-06-26",
    }


def test_live_evidence_bundle_merges_closed_historical_replay_into_calibration():
    report = build_live_evidence_bundle_report(
        queue_records=[],
        orderbook_snapshots=[],
        closed_backfill_records=[_closed_record()],
        closed_snapshot_rows=[_closed_snapshot_row()],
        replay_time="2026-06-26T11:00:00Z",
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["closed_historical_replay_report"]["hard_conclusion"] == (
        "closed_historical_replay_ready_for_settlement_calibration"
    )
    assert report["closed_historical_replay_report"]["historical_evidence"]["supplement_count"] == 1
    assert report["strict_gate_historical_evidence_report"]["supplement_count"] == 0
    assert report["historical_evidence_report"]["supplement_count"] == 1
    assert report["gap_summary"]["coverage"]["closed_historical_evidence_supplement_count"] == 1
    assert report["settlement_calibration_report"]["official_truth_sample_count"] == 1
    assert report["settlement_calibration_report"]["probability_score_sample_count"] == 1
    assert report["settlement_calibration_report"]["resolved_pnl_sample_count"] == 1
    assert report["settlement_calibration_report"]["global_calibration"]["mean_resolved_pnl_per_share"] == 0.36


def test_live_evidence_bundle_merges_preresolution_orderbook_archive_into_calibration():
    report = build_live_evidence_bundle_report(
        queue_records=[],
        orderbook_snapshots=[_archived_orderbook()],
        closed_backfill_records=[_closed_record()],
        closed_snapshot_rows=[],
        replay_time="2026-06-26T13:00:00Z",
        generated_at="2026-06-26T13:00:00Z",
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["preresolution_orderbook_replay_report"]["hard_conclusion"] == (
        "preresolution_orderbook_replay_ready_for_settlement_calibration"
    )
    assert report["preresolution_orderbook_replay_report"]["historical_evidence"]["supplement_count"] == 1
    assert report["closed_historical_replay_report"]["historical_evidence"]["supplement_count"] == 0
    assert report["historical_evidence_report"]["supplement_count"] == 1
    assert report["gap_summary"]["coverage"]["preresolution_orderbook_evidence_supplement_count"] == 1
    assert report["settlement_calibration_report"]["official_truth_sample_count"] == 1
    assert report["settlement_calibration_report"]["probability_score_sample_count"] == 1
    assert report["settlement_calibration_report"]["resolved_pnl_sample_count"] == 1
    assert report["settlement_calibration_report"]["global_calibration"]["mean_resolved_pnl_per_share"] == 0.36
    assert report["orderbook_closed_token_coverage_report"]["hard_conclusion"] == (
        "orderbook_closed_token_coverage_ready"
    )
    assert report["orderbook_closed_token_coverage_report"]["matched_closed_archived_token_count"] == 1
    assert report["gap_summary"]["coverage"]["closed_archive_token_overlap_count"] == 1


def test_live_evidence_bundle_surfaces_archived_tokens_pending_closed_backfill():
    report = build_live_evidence_bundle_report(
        queue_records=[],
        orderbook_snapshots=[_archived_orderbook(token_id="active-token")],
        closed_backfill_records=[_closed_record()],
        closed_snapshot_rows=[],
        replay_time="2026-06-26T13:00:00Z",
        generated_at="2026-06-26T13:00:00Z",
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    coverage = report["orderbook_closed_token_coverage_report"]
    assert coverage["hard_conclusion"] == "orderbook_closed_token_coverage_no_overlap"
    assert coverage["matched_closed_archived_token_count"] == 0
    assert coverage["unmatched_archived_token_count"] == 1
    assert coverage["unmatched_closed_token_count"] == 1
    assert coverage["pending_closed_backfill_due_token_count"] == 0
    assert coverage["pending_awaiting_observation_window_end_token_count"] == 1
    assert coverage["wrong_due_prevented_count"] == 1
    assert coverage["closed_backfill_followup_plan"]["request_count"] == 0
    assert coverage["closed_backfill_followup_plan"]["next_action"] == "wait_for_observation_window_end"
    assert report["official_value_backfill_report"]["target_scope"]["target_record_count"] == 0
    assert report["official_value_backfill_report"]["input_record_count"] == 0
    assert report["gap_summary"]["coverage"]["archived_pending_closed_backfill_token_count"] == 1
    assert report["gap_summary"]["coverage"]["archived_pending_closed_backfill_due_token_count"] == 0
    assert report["gap_summary"]["coverage"]["closed_missing_archive_token_count"] == 1
    gap_ids = [row["gap_id"] for row in report["gap_summary"]["gaps"]]
    assert "archived_orderbooks_pending_closed_backfill" in gap_ids


def test_live_evidence_bundle_reports_replay_and_calibration_blockers():
    report = build_live_evidence_bundle_report(
        queue_records=[_queue_record(token_id="missing-token")],
        orderbook_snapshots=[_orderbook()],
        closed_backfill_records=[_closed_record()],
        replay_time="2026-06-26T11:00:00Z",
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["hard_conclusion"] == "live_evidence_bundle_needs_more_evidence"
    assert "strict_gate_replay_needs_orderbook_archive" in report["blockers"]
    assert report["gap_summary"]["primary_blocker"] == "visible_orderbook_missing_for_replay_candidates"
    gap_ids = [row["gap_id"] for row in report["gap_summary"]["gaps"]]
    assert "historical_evidence_supplements_missing" in gap_ids
    assert "probability_score_samples_insufficient" in gap_ids
    assert "historical_evidence_supplement_count_zero" in report["blockers"]
    assert "insufficient_probability_score_samples_0_of_1" in report["blockers"]


def test_live_evidence_bundle_gap_summary_surfaces_unresolved_replay_fills():
    report = build_live_evidence_bundle_report(
        queue_records=[_queue_record()],
        orderbook_snapshots=[_orderbook()],
        closed_backfill_records=[
            _closed_record(
                token_id_by_outcome={"Yes": "other-token", "No": "no-token"},
                settled_probability_by_outcome={"Yes": 1.0, "No": 0.0},
            )
        ],
        replay_time="2026-06-26T11:00:00Z",
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["strict_gate_replay_report"]["replay"]["fill_count"] == 1
    assert report["strict_gate_replay_report"]["replay"]["missing_resolution_count"] == 1
    assert report["gap_summary"]["primary_blocker"] == "resolved_outcome_missing_for_replay_fills"
    unresolved = report["gap_summary"]["unresolved_replay"]
    assert unresolved["count"] == 1
    assert unresolved["by_strategy"] == [{"strategy_id": "tail_threshold", "count": 1}]
    assert unresolved["by_city"] == [{"city": "seoul", "count": 1}]
    assert unresolved["samples"][0]["market_slug"] == (
        "highest-temperature-in-seoul-on-june-26-2026-30c-or-above"
    )
    assert unresolved["samples"][0]["entry_price"] == 0.62


def test_live_evidence_bundle_cli_summary_only_drops_row_level_payloads(tmp_path, capsys):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    queue_dir = tmp_path / "queues"
    orderbook_dir = tmp_path / "books"
    _append_jsonl(queue_dir / "strict_gate_queue.jsonl", [_queue_record()])
    _append_jsonl(orderbook_dir / "orderbook_snapshots.jsonl", [_orderbook()])
    _append_jsonl(backfill_dir / "closed_markets.jsonl", [_closed_record()])

    bundle_cli.main(
        [
            "--paper-journal-dir",
            str(journal_dir),
            "--backfill-dir",
            str(backfill_dir),
            "--strict-gate-queue-dir",
            str(queue_dir),
            "--orderbook-archive-dir",
            str(orderbook_dir),
            "--replay-time",
            "2026-06-26T11:00:00Z",
            "--min-official-truth-samples",
            "1",
            "--min-probability-score-samples",
            "1",
            "--min-resolved-pnl-samples",
            "1",
            "--min-official-truth-coverage",
            "1.0",
            "--summary-only",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["hard_conclusion"] == "live_evidence_bundle_ready_for_readiness"
    assert "supplements" not in output["official_value_backfill_report"]
    assert "gap_samples" not in output["official_value_backfill_report"]
    assert "fills" not in output["strict_gate_replay_report"]["replay"]
    assert "supplements" not in output["strict_gate_historical_evidence_report"]
    assert "candidates" not in output["closed_historical_replay_report"]
    assert "orderbook_snapshots" not in output["closed_historical_replay_report"]
    assert "fills" not in output["closed_historical_replay_report"]["replay"]
    assert "supplements" not in output["closed_historical_replay_report"]["historical_evidence"]
    assert "candidates" not in output["preresolution_orderbook_replay_report"]
    assert "orderbook_snapshots" not in output["preresolution_orderbook_replay_report"]
    assert "fills" not in output["preresolution_orderbook_replay_report"]["replay"]
    assert "supplements" not in output["preresolution_orderbook_replay_report"]["historical_evidence"]
    assert "matched_samples" not in output["orderbook_closed_token_coverage_report"]
    assert "closed_markets_missing_archive_samples" not in output["orderbook_closed_token_coverage_report"]
    assert "archived_markets_pending_closed_backfill_samples" not in output["orderbook_closed_token_coverage_report"]
    assert "market_queries" not in output["orderbook_closed_token_coverage_report"]["closed_backfill_followup_plan"]
    assert "next_await_market_queries" not in output["orderbook_closed_token_coverage_report"]["closed_backfill_followup_plan"]
    assert "recommended_command" not in output["orderbook_closed_token_coverage_report"]["closed_backfill_followup_plan"]
    assert "requests" not in output["orderbook_closed_token_coverage_report"]["closed_backfill_followup_plan"]
    assert "await_market_end_samples" not in output["orderbook_closed_token_coverage_report"]["closed_backfill_followup_plan"]
    assert "missing_end_time_samples" not in output["orderbook_closed_token_coverage_report"]["closed_backfill_followup_plan"]
    assert "supplements" not in output["historical_evidence_report"]
    assert "calibration_rows" not in output["settlement_calibration_report"]
