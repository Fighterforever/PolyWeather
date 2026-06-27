from __future__ import annotations

import json

from scripts import weather_strict_gate_replay_report as replay_cli
from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_strict_gate_replay import (
    build_strict_gate_replay_report,
    build_strict_gate_replay_report_from_dirs,
    resolved_outcomes_from_audits_and_backfill,
)


def _queue_record(**overrides):
    row = {
        "schema_version": "polyweather_weather_strict_gate_queue.v1",
        "queue_record_id": "queue-1",
        "generated_at": "2026-06-27T00:05:00Z",
        "paper_only": True,
        "counts_for_live_gate": False,
        "queue_name": "execution_depth_price",
        "queue_reasons": ["ask_depth_below_min"],
        "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
        "token_id": "yes-token",
        "side": "yes",
        "bucket_type": "ge",
        "strategy_id": "tail_threshold",
        "execution_style": "taker_depth_checked",
        "q_effective": 0.40,
        "ev_safe": 0.03,
        "model_probability": 0.50,
    }
    row.update(overrides)
    return row


def _orderbook(**overrides):
    row = {
        "schema_version": "polyweather_polymarket_orderbook_snapshot.v1",
        "snapshot_id": "book-1",
        "recorded_at": "2026-06-27T00:04:00Z",
        "token_id": "yes-token",
        "ask_ladder": [{"price": 0.41, "size": 2.0}],
        "bid_ladder": [{"price": 0.39, "size": 2.0}],
    }
    row.update(overrides)
    return row


def test_strict_gate_replay_negative_resolved_pnl_fails_ev_audit():
    queue_records = [
        _queue_record(
            queue_record_id=f"queue-{index}",
            token_id=f"yes-token-{index}",
            market_slug=f"market-{index}",
            ev_safe=0.04,
            model_probability=0.70,
        )
        for index in range(30)
    ]
    orderbooks = [
        _orderbook(
            snapshot_id=f"book-{index}",
            token_id=f"yes-token-{index}",
            ask_ladder=[{"price": 0.60, "size": 2.0}],
        )
        for index in range(30)
    ]
    outcomes = [
        {"token_id": f"yes-token-{index}", "payout": 0.0}
        for index in range(30)
    ]

    report = build_strict_gate_replay_report(
        queue_records=queue_records,
        orderbook_snapshots=orderbooks,
        resolved_outcomes=outcomes,
        replay_time="2026-06-27T01:00:00Z",
        size=1.0,
    )

    assert report["replay"]["fill_count"] == 30
    assert report["resolved_fill_count"] == 30
    assert report["resolved_fill_coverage"] == 1.0
    assert report["positive_ev_safe_but_negative_pnl_count"] == 30
    assert report["replay"]["resolved_pnl_cents"] == -1800.0
    assert report["hard_conclusion"] == "strict_gate_replay_negative_resolved_pnl"
    assert report["by_strategy_bucket"][0]["strategy_bucket"] == "tail_threshold|ge"


def test_strict_gate_replay_reports_missing_orderbook_before_resolution():
    report = build_strict_gate_replay_report(
        queue_records=[_queue_record(), _queue_record(queue_record_id="queue-2", token_id="missing-token")],
        orderbook_snapshots=[_orderbook()],
        resolved_outcomes=[],
        replay_time="2026-06-27T01:00:00Z",
        size=1.0,
    )

    assert report["schema_version"] == "polyweather_weather_strict_gate_replay.v1"
    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["queue_record_count"] == 2
    assert report["replay_candidate_count"] == 2
    assert report["replay"]["fill_count"] == 1
    assert report["replay"]["no_visible_orderbook_count"] == 1
    assert report["replay"]["missing_resolution_count"] == 1
    assert report["execution_summary"]["mean_entry_minus_q_effective_cents"] == 1.0
    assert report["execution_summary"]["mean_ev_after_depth_cost_cents"] == 2.0
    assert report["hard_conclusion"] == "strict_gate_replay_needs_orderbook_archive"


def test_strict_gate_replay_unresolved_fills_do_not_report_zero_pnl():
    report = build_strict_gate_replay_report(
        queue_records=[_queue_record()],
        orderbook_snapshots=[_orderbook()],
        resolved_outcomes=[],
        replay_time="2026-06-27T01:00:00Z",
        size=1.0,
    )

    assert report["replay"]["fill_count"] == 1
    assert report["resolved_fill_count"] == 0
    assert report["replay"]["missing_resolution_count"] == 1
    assert report["replay"]["resolved_pnl_usdc"] is None
    assert report["replay"]["resolved_pnl_cents"] is None
    assert report["ev_audit_summary"]["resolved_pnl_cents"] is None
    assert report["hard_conclusion"] == "strict_gate_replay_needs_resolved_outcomes"


def test_strict_gate_replay_reaches_ev_audit_when_orderbook_and_resolution_exist():
    report = build_strict_gate_replay_report(
        queue_records=[_queue_record()],
        orderbook_snapshots=[_orderbook()],
        resolved_outcomes=[{"token_id": "yes-token", "payout": 1.0}],
        replay_time="2026-06-27T01:00:00Z",
        size=1.0,
    )

    assert report["hard_conclusion"] == "strict_gate_replay_ready_for_ev_audit"
    assert report["replay"]["resolved_pnl_usdc"] == 0.59
    assert report["replay"]["brier_score"] == 0.25
    assert report["resolved_fill_count"] == 1
    assert report["resolved_fill_coverage"] == 1.0
    assert report["positive_ev_safe_but_negative_pnl_count"] == 0
    assert report["execution_summary"]["by_queue"] == [
        {"queue_name": "execution_depth_price", "count": 1}
    ]
    performance = report["performance_summary"]
    assert performance["schema_version"] == "polyweather_weather_strict_gate_replay_performance.v1"
    assert performance["by_strategy"][0]["strategy_id"] == "tail_threshold"
    assert performance["by_strategy"][0]["resolved_pnl_cents"] == 59.0
    assert performance["by_strategy"][0]["win_rate"] == 1.0
    assert performance["by_bucket_type"][0]["bucket_type"] == "ge"


def test_strict_gate_replay_reports_stratified_performance_by_strategy_queue_bucket_and_city():
    report = build_strict_gate_replay_report(
        queue_records=[
            _queue_record(
                queue_record_id="queue-tail",
                token_id="tail-token",
                queue_name="ev_calibration",
                strategy_id="tail_threshold",
                bucket_type="ge",
                city="seoul",
                q_effective=0.40,
                ev_safe=0.03,
                model_probability=0.70,
            ),
            _queue_record(
                queue_record_id="queue-near",
                token_id="near-token",
                queue_name="execution_depth_price",
                strategy_id="near_lock",
                bucket_type="le",
                city="london",
                q_effective=0.60,
                ev_safe=0.02,
                model_probability=0.30,
            ),
        ],
        orderbook_snapshots=[
            _orderbook(
                snapshot_id="book-tail",
                token_id="tail-token",
                ask_ladder=[{"price": 0.40, "size": 2.0}],
            ),
            _orderbook(
                snapshot_id="book-near",
                token_id="near-token",
                ask_ladder=[{"price": 0.60, "size": 2.0}],
            ),
        ],
        resolved_outcomes=[
            {"token_id": "tail-token", "payout": 1.0},
            {"token_id": "near-token", "payout": 0.0},
        ],
        replay_time="2026-06-27T01:00:00Z",
        size=1.0,
    )

    by_strategy = {row["strategy_id"]: row for row in report["performance_summary"]["by_strategy"]}
    assert by_strategy["tail_threshold"]["resolved_pnl_cents"] == 60.0
    assert by_strategy["tail_threshold"]["win_rate"] == 1.0
    assert by_strategy["tail_threshold"]["brier_score"] == 0.09
    assert by_strategy["near_lock"]["resolved_pnl_cents"] == -60.0
    assert by_strategy["near_lock"]["win_rate"] == 0.0
    assert by_strategy["near_lock"]["brier_score"] == 0.09

    by_queue = {row["queue_name"]: row for row in report["performance_summary"]["by_queue"]}
    assert by_queue["ev_calibration"]["resolved_count"] == 1
    assert by_queue["execution_depth_price"]["resolved_pnl_cents"] == -60.0

    by_bucket = {row["bucket_type"]: row for row in report["performance_summary"]["by_bucket_type"]}
    assert by_bucket["ge"]["mean_entry_price"] == 0.4
    assert by_bucket["le"]["mean_q_effective"] == 0.6

    by_city = {row["city"]: row for row in report["performance_summary"]["by_city"]}
    assert by_city["seoul"]["resolved_pnl_cents"] == 60.0
    assert by_city["london"]["resolved_pnl_cents"] == -60.0

    by_strategy_bucket = {
        row["strategy_bucket"]: row
        for row in report["performance_summary"]["by_strategy_bucket"]
    }
    assert by_strategy_bucket["tail_threshold|ge"]["resolved_count"] == 1
    assert by_strategy_bucket["near_lock|le"]["resolved_count"] == 1


def test_resolved_outcomes_from_backfill_reconstructs_token_payouts():
    outcomes = resolved_outcomes_from_audits_and_backfill(
        audit_records=[],
        backfill_records=[
            {
                "status": "resolved",
                "market_slug": "closed-slug",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 0.0, "No": 1.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "No",
                "winning_token_id": "no-token",
                "resolution_source": "polymarket_api",
                "rule_hash": "rule-1",
                "official_final_value": 24.0,
            }
        ],
    )

    by_token = {row["token_id"]: row for row in outcomes}
    assert by_token["yes-token"]["payout"] == 0.0
    assert by_token["no-token"]["payout"] == 1.0
    assert by_token["yes-token"]["resolution_record_source"] == "closed_backfill"
    assert by_token["yes-token"]["resolution_rule_hash"] == "rule-1"


def test_strict_gate_replay_from_dirs_uses_backfill_and_prefers_audit(tmp_path):
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
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "Yes",
                "winning_token_id": "yes-token",
            }
        ],
    )
    _append_jsonl(
        journal_dir / "resolved_audits.jsonl",
        [
            {
                "status": "resolved",
                "fill_id": "fill-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "token_id": "yes-token",
                "payout": 0.0,
            }
        ],
    )

    report = build_strict_gate_replay_report_from_dirs(
        queue_dir=queue_dir,
        orderbook_archive_dir=orderbook_dir,
        journal_dir=journal_dir,
        backfill_dir=backfill_dir,
        replay_time="2026-06-27T01:00:00Z",
    )

    assert report["resolved_outcome_count"] == 2
    assert report["resolved_outcome_source_counts"] == [
        {"resolution_record_source": "closed_backfill", "count": 1},
        {"resolution_record_source": "resolved_audit", "count": 1},
    ]
    assert report["replay"]["resolved_pnl_usdc"] == -0.41
    assert report["hard_conclusion"] == "strict_gate_replay_negative_resolved_pnl"


def test_strict_gate_replay_from_dirs_supplements_backfill_tokens_from_snapshots(tmp_path):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    queue_dir = tmp_path / "queues"
    orderbook_dir = tmp_path / "books"
    _append_jsonl(queue_dir / "strict_gate_queue.jsonl", [_queue_record(token_id="no-token", side="no")])
    _append_jsonl(orderbook_dir / "orderbook_snapshots.jsonl", [_orderbook(token_id="no-token")])
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "status": "resolved",
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
                "winning_outcome": "Yes",
                "winning_token_id": "yes-token",
            }
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
                            "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                            "outcome": "Yes",
                            "token_id": "yes-token",
                            "market_probability": 1.0,
                        },
                        {
                            "market_id": "market-1",
                            "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
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

    report = build_strict_gate_replay_report_from_dirs(
        queue_dir=queue_dir,
        orderbook_archive_dir=orderbook_dir,
        journal_dir=journal_dir,
        backfill_dir=backfill_dir,
        replay_time="2026-06-27T01:00:00Z",
    )

    assert report["resolved_outcome_count"] == 2
    assert report["replay"]["missing_resolution_count"] == 0
    assert report["replay"]["resolved_pnl_usdc"] == -0.41


def test_strict_gate_replay_from_dirs_applies_official_value_supplements(tmp_path):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    queue_dir = tmp_path / "queues"
    orderbook_dir = tmp_path / "books"
    supplement_path = tmp_path / "official_values.jsonl"
    _append_jsonl(queue_dir / "strict_gate_queue.jsonl", [_queue_record()])
    _append_jsonl(orderbook_dir / "orderbook_snapshots.jsonl", [_orderbook()])
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "status": "resolved",
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "Yes",
                "winning_token_id": "yes-token",
            }
        ],
    )
    _append_jsonl(
        supplement_path,
        [
            {
                "status": "ready",
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "official_final_value": 31.2,
                "official_final_value_source": "official_test",
            }
        ],
    )

    report = build_strict_gate_replay_report_from_dirs(
        queue_dir=queue_dir,
        orderbook_archive_dir=orderbook_dir,
        journal_dir=journal_dir,
        backfill_dir=backfill_dir,
        official_value_supplements_path=supplement_path,
        replay_time="2026-06-27T01:00:00Z",
    )

    assert report["resolved_outcome_official_final_value_count"] == 2
    assert report["resolved_outcome_official_final_value_samples"] == [
        {
            "market_id": "market-1",
            "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
            "official_final_value": 31.2,
            "resolution_record_source": "closed_backfill",
            "resolution_source": "closed_backfill",
        }
    ]


def test_strict_gate_replay_cli_reads_queue_orderbook_audit_and_backfill_dirs(tmp_path, capsys):
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
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 0.0, "No": 1.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "No",
                "winning_token_id": "no-token",
            }
        ],
    )
    _append_jsonl(
        journal_dir / "resolved_audits.jsonl",
        [
            {
                "status": "resolved",
                "fill_id": "fill-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "token_id": "yes-token",
                "payout": 0.0,
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
            "2026-06-27T01:00:00Z",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["hard_conclusion"] == "strict_gate_replay_negative_resolved_pnl"
    assert output["resolved_outcome_count"] == 2
    assert output["replay"]["resolved_pnl_usdc"] == -0.41


def test_strict_gate_replay_cli_accepts_official_value_supplements(tmp_path, capsys):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    queue_dir = tmp_path / "queues"
    orderbook_dir = tmp_path / "books"
    supplement_path = tmp_path / "official_values.jsonl"
    _append_jsonl(queue_dir / "strict_gate_queue.jsonl", [_queue_record()])
    _append_jsonl(orderbook_dir / "orderbook_snapshots.jsonl", [_orderbook()])
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "status": "resolved",
                "market_id": "market-1",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "Yes",
                "winning_token_id": "yes-token",
            }
        ],
    )
    _append_jsonl(
        supplement_path,
        [
            {
                "status": "ready",
                "market_id": "market-1",
                "official_final_value": 31.2,
            }
        ],
    )

    replay_cli.main(
        [
            "--paper-journal-dir",
            str(journal_dir),
            "--backfill-dir",
            str(backfill_dir),
            "--official-value-supplements",
            str(supplement_path),
            "--strict-gate-queue-dir",
            str(queue_dir),
            "--orderbook-archive-dir",
            str(orderbook_dir),
            "--replay-time",
            "2026-06-27T01:00:00Z",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["resolved_outcome_official_final_value_count"] == 2
    assert output["resolved_outcome_official_final_value_samples"][0]["official_final_value"] == 31.2
