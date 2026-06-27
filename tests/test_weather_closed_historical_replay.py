from __future__ import annotations

import json

from scripts import weather_closed_historical_replay_report as closed_replay_cli
from scripts import weather_preresolution_orderbook_replay_report as archive_replay_cli
from src.trading.weather_closed_historical_replay import (
    build_closed_historical_replay_inputs,
    build_closed_historical_replay_report,
    build_preresolution_orderbook_replay_report,
)
from src.trading.weather_paper_journal import _append_jsonl


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
        "outcomes": ["Yes", "No"],
        "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
        "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
        "winning_outcome": "Yes",
        "winning_token_id": "yes-token",
        "official_final_value": 30.2,
    }
    row.update(overrides)
    return row


def _snapshot_row(**overrides):
    row = {
        "market_id": "market-1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-30c-or-above",
        "event_slug": "seoul-june-26-weather",
        "snapshot_recorded_at": "2026-06-26T10:00:00Z",
        "source_snapshot_id": "snapshot-1",
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
        "best_ask": 0.64,
        "best_bid": 0.60,
        "spread": 0.04,
        "ask_ladder": [{"price": 0.64, "size": 2.0}],
        "bid_ladder": [{"price": 0.60, "size": 2.0}],
    }
    row.update(overrides)
    return row


def test_closed_historical_replay_rejects_post_resolution_snapshots():
    report = build_closed_historical_replay_inputs(
        closed_records=[_closed_record()],
        snapshot_rows=[
            _snapshot_row(
                snapshot_recorded_at="2026-06-26T13:00:00Z",
                closed=True,
                accepting_orders=False,
                tradable=False,
            )
        ],
    )

    assert report["candidate_count"] == 0
    assert report["orderbook_snapshot_count"] == 0
    assert report["gaps_by_reason"] == [{"reason": "post_resolution_snapshot", "count": 1}]


def test_closed_historical_replay_builds_candidates_orderbooks_and_evidence():
    report = build_closed_historical_replay_report(
        closed_records=[_closed_record()],
        snapshot_rows=[_snapshot_row()],
        replay_time="2026-06-26T11:00:00Z",
    )

    assert report["hard_conclusion"] == "closed_historical_replay_ready_for_settlement_calibration"
    assert report["input_summary"]["candidate_count"] == 1
    assert report["input_summary"]["orderbook_snapshot_count"] == 1
    assert report["replay"]["fill_count"] == 1
    assert report["replay"]["resolved_pnl_usdc"] == 0.36
    assert report["historical_evidence"]["supplement_count"] == 1
    evidence = report["historical_evidence"]["supplements"][0]
    assert evidence["token_id"] == "yes-token"
    assert evidence["available_at"] == "2026-06-26T10:00:00Z"
    assert evidence["entry_price"] == 0.64
    assert evidence["model_probability"] == 0.62


def test_closed_historical_replay_cli_summary_and_jsonl_modes(tmp_path, capsys):
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(backfill_dir / "closed_markets.jsonl", [_closed_record()])
    snapshot_payload = {
        "schema_version": "test",
        "recorded_at": "2026-06-26T10:00:00Z",
        "payload": {
            "snapshot_id": "snapshot-1",
            "generated_at": "2026-06-26T10:00:00Z",
            "rows": [_snapshot_row(snapshot_recorded_at=None)],
        },
    }
    (backfill_dir / "snapshots").mkdir(parents=True)
    (backfill_dir / "snapshots" / "snapshot.json").write_text(
        json.dumps(snapshot_payload),
        encoding="utf-8",
    )

    closed_replay_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--replay-time",
            "2026-06-26T11:00:00Z",
            "--summary-only",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["hard_conclusion"] == "closed_historical_replay_ready_for_settlement_calibration"
    assert "candidates" not in summary
    assert "fills" not in summary["replay"]

    closed_replay_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--replay-time",
            "2026-06-26T11:00:00Z",
            "--historical-evidence-supplements-only",
        ]
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    evidence = json.loads(lines[0])
    assert evidence["schema_version"] == "polyweather_weather_historical_evidence.v1"
    assert evidence["entry_price"] == 0.64


def test_preresolution_orderbook_replay_promotes_active_archive_to_evidence():
    report = build_preresolution_orderbook_replay_report(
        closed_records=[_closed_record()],
        orderbook_snapshots=[_archived_orderbook()],
        replay_time="2026-06-26T13:00:00Z",
    )

    assert report["hard_conclusion"] == "preresolution_orderbook_replay_ready_for_settlement_calibration"
    assert report["input_summary"]["candidate_count"] == 1
    assert report["replay"]["resolved_pnl_usdc"] == 0.36
    evidence = report["historical_evidence"]["supplements"][0]
    assert evidence["source"] == "preresolution_orderbook_replay"
    assert evidence["available_at"] == "2026-06-26T10:00:00Z"
    assert evidence["model_probability"] == 0.62
    assert evidence["entry_price"] == 0.64


def test_preresolution_orderbook_replay_rejects_post_resolution_archive():
    report = build_preresolution_orderbook_replay_report(
        closed_records=[_closed_record()],
        orderbook_snapshots=[_archived_orderbook(recorded_at="2026-06-26T13:00:00Z")],
        replay_time="2026-06-26T14:00:00Z",
    )

    assert report["hard_conclusion"] == "preresolution_orderbook_replay_no_preresolution_archive"
    assert report["input_summary"]["candidate_count"] == 0
    assert report["input_summary"]["gaps_by_reason"] == [
        {"reason": "post_resolution_archive_snapshot", "count": 1}
    ]


def test_preresolution_orderbook_replay_cli_summary_and_evidence_jsonl(tmp_path, capsys):
    backfill_dir = tmp_path / "backfill"
    archive_dir = tmp_path / "orderbooks"
    _append_jsonl(backfill_dir / "closed_markets.jsonl", [_closed_record()])
    _append_jsonl(archive_dir / "orderbook_snapshots.jsonl", [_archived_orderbook()])

    archive_replay_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--replay-time",
            "2026-06-26T13:00:00Z",
            "--summary-only",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["hard_conclusion"] == "preresolution_orderbook_replay_ready_for_settlement_calibration"
    assert "candidates" not in summary
    assert "fills" not in summary["replay"]

    archive_replay_cli.main(
        [
            "--backfill-dir",
            str(backfill_dir),
            "--orderbook-archive-dir",
            str(archive_dir),
            "--replay-time",
            "2026-06-26T13:00:00Z",
            "--historical-evidence-supplements-only",
        ]
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    evidence = json.loads(lines[0])
    assert evidence["token_id"] == "yes-token"
    assert evidence["entry_price"] == 0.64
