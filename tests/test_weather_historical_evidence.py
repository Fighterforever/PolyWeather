from __future__ import annotations

import json

from scripts import weather_strict_gate_replay_report as replay_cli
from src.trading.weather_historical_evidence import historical_evidence_from_replay_report
from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_settlement_calibration import build_settlement_calibration_report
from src.trading.weather_strict_gate_replay import build_strict_gate_replay_report


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
        "official_final_value": 30.2,
        "official_final_value_source": "official_test",
    }
    row.update(overrides)
    return row


def test_historical_evidence_from_replay_report_filters_gaps_and_feeds_calibration():
    replay = build_strict_gate_replay_report(
        queue_records=[
            _queue_record(),
            _queue_record(queue_record_id="queue-missed", token_id="thin-token"),
            _queue_record(queue_record_id="queue-no-prob", token_id="noprob-token", p_lcb=None, model_probability=None),
        ],
        orderbook_snapshots=[
            _orderbook(),
            _orderbook(snapshot_id="book-thin", token_id="thin-token", ask_ladder=[]),
            _orderbook(snapshot_id="book-noprob", token_id="noprob-token"),
        ],
        resolved_outcomes=[
            {"token_id": "yes-token", "payout": 1.0},
            {"token_id": "thin-token", "payout": 1.0},
            {"token_id": "noprob-token", "payout": 1.0},
        ],
        replay_time="2026-06-26T11:00:00Z",
    )

    evidence = historical_evidence_from_replay_report(replay)

    assert evidence["supplement_count"] == 1
    assert evidence["gap_count"] == 2
    assert evidence["gaps_by_reason"] == [
        {"reason": "missed_or_partial_fill", "count": 1},
        {"reason": "missing_prediction_probability", "count": 1},
    ]
    supplement = evidence["supplements"][0]
    assert supplement["token_id"] == "yes-token"
    assert supplement["available_at"] == "2026-06-26T10:00:00Z"
    assert supplement["entry_price"] == 0.62
    assert supplement["model_probability"] == 0.8
    assert supplement["p_lcb"] == 0.75

    calibration = build_settlement_calibration_report(
        [_closed_record()],
        historical_evidence_supplements=evidence["supplements"],
        min_official_truth_samples=1,
        min_probability_score_samples=1,
        min_resolved_pnl_samples=1,
        min_official_truth_coverage=1.0,
    )

    assert calibration["hard_conclusion"] == "settlement_calibration_ready_diagnostic_only"
    assert calibration["global_calibration"]["brier_score"] == 0.04
    assert calibration["global_calibration"]["mean_resolved_pnl_per_share"] == 0.38


def test_strict_gate_replay_cli_can_emit_historical_evidence_jsonl(tmp_path, capsys):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    queue_dir = tmp_path / "queues"
    orderbook_dir = tmp_path / "books"
    _append_jsonl(queue_dir / "strict_gate_queue.jsonl", [_queue_record()])
    _append_jsonl(orderbook_dir / "orderbook_snapshots.jsonl", [_orderbook()])
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "status": "resolved",
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "Yes",
                "winning_token_id": "yes-token",
            }
        ],
    )

    replay_cli.main(
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
            "--historical-evidence-supplements-only",
        ]
    )

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["schema_version"] == "polyweather_weather_historical_evidence.v1"
    assert row["paper_only"] is True
    assert row["counts_for_live_gate"] is False
    assert row["token_id"] == "yes-token"
    assert row["entry_price"] == 0.62
