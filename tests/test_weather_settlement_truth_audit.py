from __future__ import annotations

import json

from scripts import weather_settlement_truth_audit_report as audit_cli
from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_settlement_truth_audit import (
    audit_settlement_truth_record,
    build_settlement_truth_audit_report,
    expected_yes_from_official_final_value,
)


def _record(**overrides):
    row = {
        "status": "resolved",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
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
        },
        "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
        "official_final_value": 30.2,
        "official_final_value_source": "aviationweather_metar_recent",
    }
    row.update(overrides)
    return row


def test_expected_yes_from_official_final_value_handles_threshold_buckets():
    assert expected_yes_from_official_final_value(_record())["expected_yes"] is True
    assert expected_yes_from_official_final_value(
        _record(
            bucket_label="<= 29°C",
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 29,
                "upper_threshold": None,
                "comparator": "le",
                "unit": "C",
            },
            official_final_value=28.6,
        )
    )["expected_yes"] is True


def test_expected_yes_from_official_final_value_handles_eq_and_range_with_rounding():
    assert expected_yes_from_official_final_value(
        _record(
            bucket_label="= 30°C",
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 30,
                "upper_threshold": None,
                "comparator": "eq",
                "unit": "C",
            },
            official_final_value=29.6,
        )
    )["expected_yes"] is True
    assert expected_yes_from_official_final_value(
        _record(
            bucket_label="28-30°C",
            parsed_temperature_spec={
                "city": "seoul",
                "target_date": "2026-06-26",
                "threshold": 28,
                "upper_threshold": 30,
                "comparator": "range",
                "unit": "C",
            },
            official_final_value=30.4,
        )
    )["expected_yes"] is True


def test_audit_settlement_truth_record_passes_matching_payout():
    audit = audit_settlement_truth_record(_record())

    assert audit["status"] == "pass"
    assert audit["match"] is True
    assert audit["expected_yes_payout"] == 1.0
    assert audit["settled_yes_payout"] == 1.0
    assert audit["rounded_official_final_value"] == 30.0


def test_settlement_truth_audit_report_blocks_mismatch():
    report = build_settlement_truth_audit_report(
        [
            _record(
                settled_probability_by_outcome={"Yes": 0.0, "No": 1.0},
            )
        ]
    )

    assert report["hard_conclusion"] == "settlement_truth_audit_mismatch"
    assert report["mismatch_count"] == 1
    assert report["mismatch_samples"][0]["payout_gap"] == -1.0


def test_settlement_truth_audit_cli_reads_supplements(tmp_path, capsys):
    backfill_dir = tmp_path / "backfill"
    supplement_path = tmp_path / "official_values.jsonl"
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

    audit_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--official-value-supplements",
            str(supplement_path),
            "--summary-only",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["hard_conclusion"] == "settlement_truth_audit_pass"
    assert output["audited_with_official_count"] == 1
    assert output["pass_count"] == 1
