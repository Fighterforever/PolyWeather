from __future__ import annotations

import json

from scripts import weather_closed_replay_seed_report as seed_cli
from src.trading.weather_closed_replay_seed import (
    build_closed_replay_seed_records,
    build_closed_replay_seed_report,
    build_closed_replay_seed_report_from_dir,
    supplement_closed_backfill_records_from_official_observations,
)
from src.trading.weather_paper_journal import _append_jsonl


def _backfill_record(**overrides):
    row = {
        "status": "resolved",
        "record_id": "closed-1",
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
        "event_slug": "highest-temperature-in-seoul-on-june-26-2026",
        "question": "Will the highest temperature in Seoul be 30°C or above on June 26?",
        "city": "seoul",
        "target_date": "2026-06-26",
        "bucket_label": ">= 30°C",
        "parsed_temperature_spec": {
            "city": "seoul",
            "target_date": "2026-06-26",
            "threshold": 30,
            "comparator": "ge",
            "unit": "C",
        },
        "outcomes": ["Yes", "No"],
        "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "winning_outcome": "Yes",
        "winning_token_id": "yes-token",
        "resolution_source": "polymarket_api",
        "rule_hash": "rule-hash-1",
        "official_final_value": 31.2,
        "settlement_spec": {"station": "RKSI", "metric": "daily_high_temperature"},
    }
    row.update(overrides)
    return row


def test_closed_replay_seed_records_from_complete_backfill_token_map():
    seeds = build_closed_replay_seed_records([_backfill_record()])

    assert len(seeds) == 2
    by_token = {row["token_id"]: row for row in seeds}
    assert by_token["yes-token"]["payout"] == 1.0
    assert by_token["yes-token"]["winning"] is True
    assert by_token["no-token"]["payout"] == 0.0
    assert by_token["yes-token"]["paper_only"] is True
    assert by_token["yes-token"]["counts_for_live_gate"] is False
    assert by_token["yes-token"]["live_gate_excluded"] is True
    assert by_token["yes-token"]["diagnostic_only"] is True
    assert by_token["yes-token"]["bucket_type"] == "ge"


def test_closed_replay_seed_report_flags_partial_token_coverage_and_missing_rule_fields():
    report = build_closed_replay_seed_report(
        [
            _backfill_record(
                token_id_by_outcome=None,
                winning_outcome="No",
                winning_token_id="no-token",
                settled_probability_by_outcome={"Yes": 0.0, "No": 1.0},
                rule_hash=None,
                official_final_value=None,
            )
        ]
    )

    assert report["hard_conclusion"] == "closed_replay_seed_partial_token_coverage"
    assert report["replay_seed_token_count"] == 1
    assert report["complete_token_market_count"] == 0
    assert report["partial_token_market_count"] == 1
    assert report["missing_token_map_market_count"] == 1
    assert report["missing_payout_market_count"] == 0
    assert report["missing_rule_hash_count"] == 1
    assert report["missing_official_final_value_count"] == 1
    assert report["gap_samples"][0]["missing_token_outcomes"] == ["Yes"]
    assert report["gap_samples"][0]["has_rule_hash"] is False
    assert report["gap_samples"][0]["has_official_final_value"] is False


def test_closed_replay_seed_report_blocks_complete_tokens_without_settlement_truth():
    report = build_closed_replay_seed_report(
        [
            _backfill_record(
                rule_hash=None,
                official_final_value=None,
            )
        ]
    )

    assert report["hard_conclusion"] == "closed_replay_seed_missing_rule_hash"
    assert report["replay_seed_token_count"] == 2
    assert report["complete_token_market_count"] == 1
    assert report["missing_token_map_market_count"] == 0
    assert report["missing_payout_market_count"] == 0
    assert report["missing_rule_hash_count"] == 1
    assert report["missing_official_final_value_count"] == 1


def test_closed_replay_seed_report_rebuilds_rule_hash_without_faking_official_value():
    report = build_closed_replay_seed_report(
        [
            _backfill_record(
                bucket_label="= 30°C",
                parsed_temperature_spec={
                    "city": "seoul",
                    "target_date": "2026-06-26",
                    "threshold": 30,
                    "comparator": "eq",
                    "unit": "C",
                },
                end_date="2026-06-26T12:00:00Z",
                rule_hash=None,
                official_final_value=None,
            )
        ]
    )

    assert report["hard_conclusion"] == "closed_replay_seed_missing_official_final_value"
    assert report["missing_rule_hash_count"] == 0
    assert report["missing_official_final_value_count"] == 1
    assert report["market_inferred_final_value_count"] == 1
    assert report["seeds"][0]["resolution_rule_hash"]
    assert report["seeds"][0]["official_final_value"] is None
    assert report["seeds"][0]["market_inferred_final_value"] == 30.0


def test_closed_replay_seed_cli_reads_backfill_dir(tmp_path, capsys):
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(backfill_dir / "closed_markets.jsonl", [_backfill_record()])

    seed_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--summary-only",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["hard_conclusion"] == "closed_replay_seed_ready"
    assert output["replay_seed_token_count"] == 2
    assert "seeds" not in output


def test_closed_replay_seed_from_dir_supplements_token_map_from_snapshots(tmp_path):
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            _backfill_record(
                token_id_by_outcome=None,
                winning_token_id="yes-token",
            )
        ],
    )
    (backfill_dir / "snapshots").mkdir(parents=True)
    (backfill_dir / "snapshots" / "snapshot.json").write_text(
        json.dumps(
            {
                "payload": {
                    "rows": [
                        {
                            "market_id": "market-1",
                            "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
                            "outcome": "Yes",
                            "token_id": "yes-token",
                            "market_probability": 1.0,
                        },
                        {
                            "market_id": "market-1",
                            "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
                            "outcome": "No",
                            "token_id": "no-token",
                            "market_probability": 0.0,
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    report = build_closed_replay_seed_report_from_dir(backfill_dir)

    assert report["hard_conclusion"] == "closed_replay_seed_ready"
    assert report["snapshot_supplemented_market_count"] == 1
    assert report["complete_token_market_count"] == 1
    assert report["missing_token_map_market_count"] == 0
    assert report["replay_seed_token_count"] == 2


def test_closed_replay_seed_supplements_official_value_from_observation_store():
    class FakeRepository:
        def load_points(self, *, source_code, station_code, target_date):
            assert source_code == "metar"
            assert station_code == "RKSI"
            assert target_date == "2026-06-26"
            return [
                {"time": "12:00", "temp": 29.1},
                {"time": "15:00", "temp": 31.2},
            ]

    records = supplement_closed_backfill_records_from_official_observations(
        [
            _backfill_record(
                official_final_value=None,
                settlement_spec={
                    "station_code": "RKSI",
                    "settlement_source": "metar",
                    "target_date": "2026-06-26",
                },
            )
        ],
        repository=FakeRepository(),
    )

    assert records[0]["official_final_value"] == 31.2
    assert records[0]["official_final_value_source"] == "official_intraday_observation_store"
    assert records[0]["official_observation_supplement_applied"] is True

    report = build_closed_replay_seed_report(records)
    assert report["hard_conclusion"] == "closed_replay_seed_ready"
    assert report["missing_official_final_value_count"] == 0
    assert report["official_observation_supplemented_market_count"] == 1


def test_closed_replay_seed_keeps_gap_when_observation_store_has_no_points():
    class EmptyRepository:
        def load_points(self, *, source_code, station_code, target_date):
            return []

    records = supplement_closed_backfill_records_from_official_observations(
        [
            _backfill_record(
                official_final_value=None,
                settlement_spec={
                    "station_code": "RKSI",
                    "settlement_source": "metar",
                    "target_date": "2026-06-26",
                },
            )
        ],
        repository=EmptyRepository(),
    )

    assert records[0]["official_final_value"] is None
    assert records[0]["official_final_value_gap_reason"] == "missing_observation_points"

    report = build_closed_replay_seed_report(records)
    assert report["hard_conclusion"] == "closed_replay_seed_missing_official_final_value"
    assert report["official_observation_gap_count"] == 1
    assert report["official_observation_gaps_by_reason"] == [
        {"reason": "missing_observation_points", "count": 1}
    ]
