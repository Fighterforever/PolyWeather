from __future__ import annotations

from src.trading.weather_temperature_execution_experiment import (
    build_temperature_execution_shadow_validation_report,
    build_temperature_execution_quote_records,
    build_temperature_execution_experiment_report,
    build_temperature_taker_paper_signal_report,
    build_temperature_taker_paper_validation_report,
    run_temperature_taker_paper_cycle,
    write_temperature_execution_quotes,
)
from src.trading.weather_paper_journal import _append_jsonl, load_jsonl


def test_temperature_execution_experiment_collects_offset_ladder_for_negative_maker():
    report = build_temperature_execution_experiment_report(
        {
            "opportunity_id": "opp-1",
            "source_snapshot_id": "snap-1",
            "blocked_by_negative_maker_evidence": [
                {
                    "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                    "market_id": "2679215",
                    "token_id": "token-no",
                    "city": "paris",
                    "side": "no",
                    "outcome": "No",
                    "bucket_type": "le",
                    "bucket_label": "<= 37C",
                    "price": 0.17,
                    "bid": 0.16,
                    "ask": 0.17,
                    "spread": 0.01,
                    "edge_percent": 14.1,
                    "maker_hit_details": [
                        {
                            "failure_reasons": [
                                "mean_maker_markout_below_threshold",
                                "maker_win_rate_below_threshold",
                            ],
                            "evidence": {
                                "inferred_fill_count": 3,
                                "fill_inference_rate": 0.333333,
                                "mean_maker_markout_cents": -3.0,
                                "maker_markout_win_rate": 0.0,
                                "mean_missed_taker_markout_cents": -0.25,
                            },
                        }
                    ],
                }
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_temperature_execution_experiment.v1"
    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_gate"] is False
    assert report["hard_conclusion"] == "temperature_execution_experiment_collect_offset_ladder"
    assert report["collect_offset_ladder_shadow_quote_count"] == 1
    row = report["experiments"][0]
    assert row["formal_paper_eligible"] is False
    assert row["next_action"] == "collect_offset_ladder_shadow_quotes"
    assert row["taker_cross"]["promotion_ready"] is False
    assert "missed_taker_proxy_not_positive" in row["taker_cross"]["promotion_blockers"]
    assert row["maker_offset_ladder"][-1]["quote_offset_cents"] == 3.0
    assert row["maker_offset_ladder"][-1]["quote_price"] == 0.13
    assert row["maker_offset_ladder"][-1]["improvement_covers_observed_maker_adverse_selection"] is True


def test_temperature_execution_experiment_skips_when_no_strategy_covers_adverse():
    report = build_temperature_execution_experiment_report(
        {
            "opportunity_id": "opp-2",
            "blocked_by_negative_maker_evidence": [
                {
                    "market_slug": "market",
                    "side": "no",
                    "bucket_type": "le",
                    "price": 0.17,
                    "bid": 0.16,
                    "ask": 0.17,
                    "spread": 0.01,
                    "maker_hit_details": [
                        {
                            "evidence": {
                                "inferred_fill_count": 3,
                                "mean_maker_markout_cents": -5.0,
                                "maker_markout_win_rate": 0.0,
                                "mean_missed_taker_markout_cents": -1.0,
                            },
                        }
                    ],
                }
            ],
        },
        offset_cents=(0.0, 1.0, 2.0),
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "temperature_execution_experiment_skip_negative_maker_surface"
    assert report["skip_current_surface_count"] == 1
    assert report["experiments"][0]["next_action"] == "skip_until_surface_changes_or_settlement"


def test_temperature_execution_experiment_prompts_taker_paper_when_proxy_is_positive():
    report = build_temperature_execution_experiment_report(
        {
            "opportunity_id": "opp-3",
            "blocked_by_negative_maker_evidence": [
                {
                    "market_slug": "market",
                    "side": "no",
                    "bucket_type": "le",
                    "price": 0.17,
                    "bid": 0.16,
                    "ask": 0.17,
                    "spread": 0.01,
                    "maker_hit_details": [
                        {
                            "evidence": {
                                "inferred_fill_count": 3,
                                "mean_maker_markout_cents": -1.0,
                                "maker_markout_win_rate": 0.0,
                                "mean_missed_taker_markout_cents": 0.25,
                            },
                        }
                    ],
                }
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "temperature_execution_experiment_collect_taker_paper"
    assert report["collect_formal_taker_paper_count"] == 1
    assert report["experiments"][0]["next_action"] == "collect_formal_taker_paper"


def test_temperature_execution_quote_records_and_write_dedupe(tmp_path):
    experiment_report = build_temperature_execution_experiment_report(
        {
            "opportunity_id": "opp-4",
            "source_snapshot_id": "snap-4",
            "blocked_by_negative_maker_evidence": [
                {
                    "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                    "market_id": "2679215",
                    "token_id": "token-no",
                    "city": "paris",
                    "side": "no",
                    "outcome": "No",
                    "bucket_type": "le",
                    "bucket_label": "<= 37C",
                    "price": 0.17,
                    "bid": 0.16,
                    "ask": 0.17,
                    "spread": 0.01,
                    "edge_percent": 14.1,
                    "end_date": "2026-06-27T12:00:00Z",
                    "maker_hit_details": [
                        {
                            "evidence": {
                                "inferred_fill_count": 3,
                                "mean_maker_markout_cents": -3.0,
                                "maker_markout_win_rate": 0.0,
                                "mean_missed_taker_markout_cents": -0.25,
                            },
                        }
                    ],
                }
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    records = build_temperature_execution_quote_records(
        experiment_report,
        recorded_at="2026-06-27T00:01:00Z",
    )

    assert len(records) == 5
    assert records[0]["schema_version"] == "polyweather_weather_maker_quote.v1"
    assert records[0]["temperature_execution_experiment"] is True
    assert records[0]["token_id"] == "token-no"
    assert records[0]["time_to_expiry_bucket"] == "6-24h"
    assert records[0]["fine_time_to_expiry_bucket"] != "missing"
    assert records[-1]["quote_strategy"] == "maker_bid_minus_3c"
    assert records[-1]["quote_price"] == 0.13

    first = write_temperature_execution_quotes(
        experiment_report,
        journal_dir=tmp_path,
        recorded_at="2026-06-27T00:01:00Z",
    )
    second = write_temperature_execution_quotes(
        experiment_report,
        journal_dir=tmp_path,
        recorded_at="2026-06-27T00:02:00Z",
    )

    assert first["quote_records_written"] == 5
    assert first["duplicate_skipped_count"] == 0
    assert second["quote_records_written"] == 0
    assert second["duplicate_skipped_count"] == 5


def test_temperature_execution_shadow_validation_routes_positive_taker_proxy(tmp_path):
    rows = [
        {
            "schema_version": "polyweather_weather_maker_quote_markout.v1",
            "quote_id": f"quote-{index}",
            "recorded_at": "2026-06-27T02:35:15Z",
            "status": "resting_unfilled",
            "quote_strategy": f"maker_bid_minus_{index}c" if index else "maker_bid",
            "quote_price": 0.16 - index * 0.01,
            "entry_spread_bucket": "0.005-0.015",
            "fine_time_to_expiry_bucket": "6-12h",
            "city": "paris",
            "market_family": "temperature",
            "bucket_type": "le",
            "missed_taker_markout_cents": 1.0,
        }
        for index in range(5)
    ]
    _append_jsonl(tmp_path / "maker_quote_markouts.jsonl", rows)

    report = build_temperature_execution_shadow_validation_report(
        journal_dir=tmp_path,
        generated_at="2026-06-27T02:40:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_temperature_execution_shadow_validation.v1"
    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["live_gate"] is False
    assert report["hard_conclusion"] == "temperature_execution_shadow_taker_proxy_positive_maker_unfilled"
    assert report["next_action"] == "collect_formal_taker_paper"
    assert report["summary"]["quote_markout_count"] == 5
    assert report["summary"]["inferred_fill_count"] == 0
    assert report["summary"]["mean_missed_taker_markout_cents"] == 1.0
    assert "maker_offset_fill_probability_unproven" in report["blockers"]


def test_temperature_taker_paper_signal_report_uses_execution_quotes_after_validation(tmp_path):
    _append_jsonl(
        tmp_path / "maker_quotes.jsonl",
        [
            {
                "schema_version": "polyweather_weather_maker_quote.v1",
                "quote_id": "quote-1",
                "run_id": "run-1",
                "recorded_at": "2026-06-27T02:29:11Z",
                "status": "open",
                "temperature_execution_experiment": True,
                "city": "paris",
                "market_family": "temperature",
                "question": "Will the highest temperature in Paris be 37C or below?",
                "market_id": "2679215",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "token_id": "token-no",
                "side": "no",
                "outcome": "No",
                "bucket_label": "<= 37C",
                "bucket_type": "le",
                "entry_ask": 0.17,
                "entry_bid": 0.16,
                "entry_spread": 0.01,
                "entry_liquidity": 100.0,
                "edge_percent": 14.1,
                "model_probability": 0.311,
                "market_probability": 0.165,
                "end_date": "2026-06-27T12:00:00Z",
            },
            {
                "schema_version": "polyweather_weather_maker_quote.v1",
                "quote_id": "quote-2",
                "run_id": "run-1",
                "recorded_at": "2026-06-27T02:29:11Z",
                "status": "open",
                "temperature_execution_experiment": True,
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "token_id": "token-no",
                "side": "no",
                "entry_ask": 0.17,
                "quote_strategy": "maker_bid_minus_1c",
            },
        ],
    )
    validation = {
        "validation_id": "validation-1",
        "hard_conclusion": "temperature_execution_shadow_taker_proxy_positive_maker_unfilled",
        "next_action": "collect_formal_taker_paper",
    }

    report = build_temperature_taker_paper_signal_report(
        execution_journal_dir=tmp_path,
        validation_report=validation,
        generated_at="2026-06-27T02:40:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_temperature_taker_paper.v1"
    assert report["hard_conclusion"] == "temperature_taker_paper_collect_formal"
    assert report["summary"]["formal_taker_paper_count"] == 1
    row = report["quarantine"][0]
    assert row["formal_taker_paper"] is True
    assert row["execution_mode"] == "taker_cross"
    assert row["price"] == 0.17
    assert row["counts_for_live_gate"] is False
    assert row["live_gate_excluded"] is True
    assert row["quarantine_reason"] == "formal_taker_execution_probe"


def test_run_temperature_taker_paper_cycle_writes_probe_and_marks(monkeypatch, tmp_path):
    execution_dir = tmp_path / "execution"
    taker_dir = tmp_path / "taker"
    _append_jsonl(
        execution_dir / "maker_quotes.jsonl",
        [
            {
                "schema_version": "polyweather_weather_maker_quote.v1",
                "quote_id": "quote-1",
                "run_id": "run-1",
                "recorded_at": "2026-06-27T02:29:11Z",
                "status": "open",
                "temperature_execution_experiment": True,
                "city": "paris",
                "market_family": "temperature",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "token_id": "token-no",
                "side": "no",
                "outcome": "No",
                "bucket_label": "<= 37C",
                "entry_ask": 0.17,
                "entry_bid": 0.16,
                "entry_spread": 0.01,
                "edge_percent": 14.1,
                "end_date": "2026-06-27T12:00:00Z",
            }
        ],
    )
    _append_jsonl(
        execution_dir / "maker_quote_markouts.jsonl",
        [
            {
                "schema_version": "polyweather_weather_maker_quote_markout.v1",
                "quote_id": f"quote-{index}",
                "recorded_at": "2026-06-27T02:35:15Z",
                "status": "resting_unfilled",
                "quote_strategy": f"maker_bid_minus_{index}c" if index else "maker_bid",
                "city": "paris",
                "market_family": "temperature",
                "bucket_type": "le",
                "entry_spread_bucket": "0.005-0.015",
                "fine_time_to_expiry_bucket": "6-12h",
                "missed_taker_markout_cents": 1.0,
            }
            for index in range(5)
        ],
    )

    def fake_markout(**kwargs):
        fills = load_jsonl(taker_dir / "paper_fills.jsonl")
        _append_jsonl(
            taker_dir / "markouts.jsonl",
            [
                {
                    "schema_version": "polyweather_weather_paper_markout.v1",
                    "fill_id": fills[0]["fill_id"],
                    "recorded_at": "2026-06-27T02:40:00Z",
                    "status": "marked",
                    "markout_cents": 1.0,
                    "markout_horizon": "0-5m",
                }
            ],
        )
        return {
            "journal_dir": str(kwargs["journal_dir"]),
            "marked_count": 1,
            "mean_markout_cents": 1.0,
            "records": [{"private": "omitted"}],
        }

    monkeypatch.setattr(
        "src.trading.weather_temperature_execution_experiment.markout_open_paper_fills",
        fake_markout,
    )

    report = run_temperature_taker_paper_cycle(
        execution_journal_dir=execution_dir,
        taker_journal_dir=taker_dir,
        generated_at="2026-06-27T02:40:00Z",
        min_reentry_seconds=3600,
    )

    assert report["hard_conclusion"] == "temperature_taker_paper_collect_formal"
    assert report["journal"]["fill_count"] == 1
    assert report["markout"]["marked_count"] == 1
    assert report["markout"]["mean_markout_cents"] == 1.0
    assert report["taker_validation"]["hard_conclusion"] == "temperature_taker_validation_collect_more_markouts"


def test_temperature_taker_validation_requires_sample_size_and_horizons(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            {
                "schema_version": "polyweather_weather_paper_fill.v1",
                "fill_id": "fill-1",
                "status": "open",
                "recorded_at": "2026-06-27T02:41:44Z",
                "market_slug": "market",
                "token_id": "token",
                "side": "no",
                "entry_price": 0.17,
                "signal_bucket": "quarantine",
                "quarantine_reason": "formal_taker_execution_probe",
            }
        ],
    )
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {
                "schema_version": "polyweather_weather_paper_markout.v1",
                "fill_id": "fill-1",
                "recorded_at": "2026-06-27T02:42:00Z",
                "status": "marked",
                "markout_cents": 1.0,
                "markout_horizon": "0-5m",
            }
        ],
    )

    report = build_temperature_taker_paper_validation_report(
        journal_dir=tmp_path,
        generated_at="2026-06-27T02:43:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_temperature_taker_paper_validation.v1"
    assert report["hard_conclusion"] == "temperature_taker_validation_collect_more_markouts"
    assert report["next_action"] == "continue_taker_paper_markouts"
    assert "insufficient_marked_count_1_of_10" in report["blockers"]
    assert "required_horizon_samples_missing" in report["blockers"]
    assert sorted(report["missing_horizons"]) == ["0-5m", "15-30m", "5-15m"]


def test_temperature_taker_validation_waits_for_resolution_after_forward_evidence(tmp_path):
    fills = []
    markouts = []
    for index in range(10):
        fill_id = f"fill-{index}"
        fills.append(
            {
                "schema_version": "polyweather_weather_paper_fill.v1",
                "fill_id": fill_id,
                "status": "open",
                "recorded_at": "2026-06-27T02:00:00Z",
                "market_slug": f"market-{index}",
                "token_id": f"token-{index}",
                "side": "no",
                "entry_price": 0.17,
                "signal_bucket": "quarantine",
                "quarantine_reason": "formal_taker_execution_probe",
            }
        )
        for horizon in ("0-5m", "5-15m", "15-30m"):
            markouts.append(
                {
                    "schema_version": "polyweather_weather_paper_markout.v1",
                    "fill_id": fill_id,
                    "recorded_at": f"2026-06-27T02:{index:02d}:00Z",
                    "status": "marked",
                    "markout_cents": 1.0,
                    "markout_horizon": horizon,
                }
            )
    _append_jsonl(tmp_path / "paper_fills.jsonl", fills)
    _append_jsonl(tmp_path / "markouts.jsonl", markouts)
    _append_jsonl(
        tmp_path / "resolved_audits.jsonl",
        [
            {
                "fill_id": fill["fill_id"],
                "status": "unresolved",
            }
            for fill in fills
        ],
    )

    report = build_temperature_taker_paper_validation_report(
        journal_dir=tmp_path,
        min_marked_count=10,
        min_horizon_count=3,
        min_resolved_count=1,
        generated_at="2026-06-27T03:00:00Z",
    )

    assert report["hard_conclusion"] == "temperature_taker_validation_wait_resolved_audit"
    assert report["next_action"] == "collect_resolved_audit"
    assert report["summary"]["marked_count"] == 10
    assert report["summary"]["mean_markout_cents"] == 1.0
    assert report["summary"]["win_rate"] == 1.0
    assert report["summary"]["resolved_count"] == 0
    assert report["missing_horizons"] == []
    assert "insufficient_resolved_count_0_of_1" in report["blockers"]
