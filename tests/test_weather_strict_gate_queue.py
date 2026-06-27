from __future__ import annotations

from src.trading.weather_paper_journal import load_jsonl
from src.trading.weather_strict_gate_queue import (
    build_strict_gate_queue_records,
    strict_gate_queue_records_as_replay_candidates,
    summarize_strict_gate_queue_journal,
    write_strict_gate_queue_journal,
)


def _signal_report():
    return {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "source_snapshot_id": "scan-1",
        "source_status": "ready",
        "strict_gate_diagnostics": {
            "schema_version": "polyweather_weather_strict_gate_diagnostics.v1",
            "targeted_paper_queues": {
                "ev_calibration": {
                    "description": "calibrate EV",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "row_count": 1,
                    "items": [
                        {
                            "decision": "reject",
                            "queue_name": "ev_calibration",
                            "queue_reasons": ["ev_safe_below_min"],
                            "city": "seoul",
                            "market_family": "temperature",
                            "market_id": "m1",
                            "market_slug": "highest-temperature-in-seoul-on-june-27-2026-28c-or-above",
                            "token_id": "yes-token",
                            "side": "yes",
                            "outcome": "Yes",
                            "bucket_type": "ge",
                            "bucket_label": ">= 28°C",
                            "strategy_id": "tail_threshold",
                            "execution_style": "taker_depth_checked",
                            "strategy_live_eligible": True,
                            "price": 0.31,
                            "bid": 0.29,
                            "ask": 0.31,
                            "spread": 0.02,
                            "liquidity": 42,
                            "bid_depth_usdc_3c": 8,
                            "ask_depth_usdc_3c": 22,
                            "edge_percent": 4.0,
                            "model_probability": 0.36,
                            "market_probability": 0.30,
                            "p_lcb": 0.30,
                            "q_effective": 0.31,
                            "cost": 0.005,
                            "ev_safe": -0.015,
                            "non_risk_blockers": ["ev_safe_below_min"],
                            "non_risk_blocker_categories": ["edge"],
                            "risk_rule_hits": [],
                        }
                    ],
                },
                "risk_rule_review": {
                    "description": "risk review",
                    "paper_only": True,
                    "counts_for_live_gate": False,
                    "row_count": 1,
                    "items": [
                        {
                            "decision": "reject",
                            "queue_name": "risk_rule_review",
                            "queue_reasons": ["negative_markout_rule:by_city:city=seoul"],
                            "market_slug": "risk-only",
                            "side": "yes",
                            "risk_rule_hits": ["negative_markout_rule:by_city:city=seoul"],
                        }
                    ],
                },
            },
        },
    }


def test_build_strict_gate_queue_records_are_paper_only_replay_inputs():
    records = build_strict_gate_queue_records(
        _signal_report(),
        generated_at="2026-06-27T00:00:00Z",
        source="test",
    )

    assert len(records) == 2
    first = records[0]
    assert first["schema_version"] == "polyweather_weather_strict_gate_queue.v1"
    assert first["paper_only"] is True
    assert first["counts_for_live_gate"] is False
    assert first["live_gate_excluded"] is True
    assert first["queue_name"] == "ev_calibration"
    assert first["token_id"] == "yes-token"
    assert first["ev_safe"] == -0.015

    replay_candidates = strict_gate_queue_records_as_replay_candidates(records)
    assert len(replay_candidates) == 1
    assert replay_candidates[0]["row_id"] == first["queue_record_id"]
    assert replay_candidates[0]["available_at"] == "2026-06-27T00:00:00Z"
    assert replay_candidates[0]["counts_for_live_gate"] is False


def test_write_strict_gate_queue_journal_round_trips_manifest_and_summary(tmp_path):
    result = write_strict_gate_queue_journal(
        _signal_report(),
        queue_dir=tmp_path,
        generated_at="2026-06-27T00:00:00Z",
        source="test",
    )

    assert result["schema_version"] == "polyweather_weather_strict_gate_queue.v1"
    assert result["paper_only"] is True
    assert result["counts_for_live_gate"] is False
    assert result["record_count"] == 2
    assert {row["queue_name"]: row["count"] for row in result["queue_counts"]} == {
        "ev_calibration": 1,
        "risk_rule_review": 1,
    }

    records = load_jsonl(tmp_path / "strict_gate_queue.jsonl")
    manifest = load_jsonl(tmp_path / "manifest.jsonl")
    summary = summarize_strict_gate_queue_journal(tmp_path)
    assert records[0]["queue_name"] == "ev_calibration"
    assert manifest[-1]["run_id"] == result["run_id"]
    assert summary["record_count"] == 2
    assert summary["manifest_count"] == 1
