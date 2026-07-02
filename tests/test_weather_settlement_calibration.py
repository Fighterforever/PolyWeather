from __future__ import annotations

import json

from scripts import weather_settlement_calibration_report as calibration_cli
from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_settlement_calibration import build_settlement_calibration_report


def _record(**overrides):
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
        "official_final_value": 30.2,
        "official_final_value_source": "aviationweather_metar_recent",
    }
    row.update(overrides)
    return row


def test_settlement_calibration_groups_official_truth_and_probability_scores():
    report = build_settlement_calibration_report(
        [
            _record(model_probability=0.80, historical_entry_price=0.62),
            _record(
                market_id="market-2",
                market_slug="highest-temperature-in-seoul-on-june-26-2026-29c-or-below",
                bucket_label="<= 29°C",
                parsed_temperature_spec={
                    "city": "seoul",
                    "target_date": "2026-06-26",
                    "threshold": 29,
                    "upper_threshold": None,
                    "comparator": "le",
                    "unit": "C",
                },
                settled_probability_by_outcome={"Yes": 0.0, "No": 1.0},
                official_final_value=30.2,
                model_probability=0.35,
                historical_entry_price=0.22,
            ),
        ],
        min_official_truth_samples=2,
        min_probability_score_samples=2,
        min_resolved_pnl_samples=2,
        min_official_truth_coverage=1.0,
    )

    assert report["hard_conclusion"] == "settlement_calibration_ready_diagnostic_only"
    assert report["official_truth_sample_count"] == 2
    assert report["probability_score_sample_count"] == 2
    assert report["resolved_pnl_sample_count"] == 2
    assert report["global_calibration"]["yes_rate"] == 0.5
    assert report["global_calibration"]["brier_score"] == 0.08125
    assert report["global_calibration"]["mean_resolved_pnl_per_share"] == 0.08
    assert any(
        group["group_type"] == "bucket_type" and group["group_key"] == "ge"
        for group in report["groups"]
    )


def test_settlement_calibration_keeps_missing_probability_and_pnl_as_blockers():
    report = build_settlement_calibration_report(
        [_record()],
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["hard_conclusion"] == "insufficient_probability_score_samples_0_of_1"
    assert "insufficient_resolved_pnl_samples_0_of_1" in report["blockers"]
    assert report["probability_evidence_available"] is False
    assert report["probability_score_gap_reason"] == "missing_historical_forecast_probability"
    assert report["resolved_pnl_available"] is False
    assert report["resolved_pnl_gap_reason"] == "missing_historical_entry_price"
    assert report["calibration_rows"][0]["resolved_pnl_gap_reason"] == "missing_historical_entry_price"


def test_settlement_calibration_applies_visible_historical_evidence_supplements():
    report = build_settlement_calibration_report(
        [_record(official_final_value=30.2)],
        historical_evidence_supplements=[
            {
                "source": "closed_replay_probe",
                "token_id": "yes-token",
                "side": "Yes",
                "available_at": "2026-06-26T10:00:00Z",
                "model_probability": 0.8,
                "entry_price": 0.6,
                "orderbook_snapshot_id": "book-visible",
            }
        ],
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert report["hard_conclusion"] == "settlement_calibration_ready_diagnostic_only"
    assert report["historical_evidence_supplement_summary"]["applied_record_count"] == 1
    assert report["historical_evidence_no_lookahead"] is True
    assert report["global_calibration"]["brier_score"] == 0.04
    assert report["global_calibration"]["mean_resolved_pnl_per_share"] == 0.4
    row = report["calibration_rows"][0]
    assert row["historical_evidence_no_lookahead"] is True
    assert row["historical_evidence_orderbook_snapshot_id"] == "book-visible"
    assert row["prediction_probability"] == 0.8
    assert row["entry_price"] == 0.6


def test_settlement_calibration_rejects_future_historical_evidence():
    report = build_settlement_calibration_report(
        [_record()],
        historical_evidence_supplements=[
            {
                "source": "future_probe",
                "token_id": "yes-token",
                "side": "Yes",
                "available_at": "2026-06-26T13:00:00Z",
                "model_probability": 0.99,
                "entry_price": 0.01,
            }
        ],
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    summary = report["historical_evidence_supplement_summary"]
    assert summary["applied_record_count"] == 0
    assert summary["future_evidence_count"] == 1
    assert report["historical_evidence_no_lookahead"] is False
    assert report["probability_score_sample_count"] == 0
    assert report["resolved_pnl_sample_count"] == 0
    assert report["hard_conclusion"] == "insufficient_probability_score_samples_0_of_1"


def test_settlement_calibration_blocks_truth_mismatch():
    report = build_settlement_calibration_report(
        [_record(settled_probability_by_outcome={"Yes": 0.0, "No": 1.0})],
        min_official_truth_samples=1,
        min_probability_score_samples=0,
        min_resolved_pnl_samples=0,
        min_official_truth_coverage=0.0,
    )

    assert report["hard_conclusion"] == "settlement_calibration_truth_mismatch"
    assert report["mismatch_count"] == 1
    assert report["official_truth_sample_count"] == 0


def test_settlement_calibration_cli_reads_official_value_supplements(tmp_path, capsys):
    backfill_dir = tmp_path / "backfill"
    supplement_path = tmp_path / "official_values.jsonl"
    evidence_path = tmp_path / "historical_evidence.jsonl"
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            _record(
                official_final_value=None,
                market_id="market-1",
                market_slug="highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
            )
        ],
    )
    _append_jsonl(
        supplement_path,
        [
            {
                "status": "ready",
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
                "official_final_value": 30.2,
                "official_final_value_source": "official_test",
            }
        ],
    )
    _append_jsonl(
        evidence_path,
        [
            {
                "source": "closed_replay_probe",
                "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
                "side": "Yes",
                "available_at": "2026-06-26T10:00:00Z",
                "model_probability": 0.8,
                "entry_price": 0.6,
                "orderbook_snapshot_id": "book-visible",
            }
        ],
    )

    calibration_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--official-value-supplements",
            str(supplement_path),
            "--historical-evidence-supplements",
            str(evidence_path),
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
    assert output["hard_conclusion"] == "settlement_calibration_ready_diagnostic_only"
    assert output["official_truth_sample_count"] == 1
    assert output["probability_score_sample_count"] == 1
    assert output["resolved_pnl_sample_count"] == 1
    assert output["historical_evidence_supplement_summary"]["applied_record_count"] == 1
    assert "calibration_rows" not in output
