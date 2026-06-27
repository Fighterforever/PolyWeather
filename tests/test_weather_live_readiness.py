from __future__ import annotations

import json

from src.trading.weather_live_readiness import build_live_readiness_report, load_signal_report
from src.trading.weather_paper_journal import _append_jsonl


def _signal_report(candidate_count=1):
    return {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "summary": {"candidate_count": candidate_count, "watch_count": 0},
    }


def _fill(
    index: int,
    *,
    signal_bucket: str = "candidates",
    counts_for_live_gate: bool = True,
    **overrides,
):
    row = {
        "schema_version": "polyweather_weather_paper_fill.v1",
        "fill_id": f"fill-{index}",
        "status": "open",
        "signal_bucket": signal_bucket,
        "entry_price": 0.25,
        "counts_for_live_gate": counts_for_live_gate,
    }
    row.update(overrides)
    return row


def _markout(index: int, *, markout_cents: float = 2.0):
    return {
        "schema_version": "polyweather_weather_paper_markout.v1",
        "fill_id": f"fill-{index}",
        "status": "marked",
        "markout_cents": markout_cents,
    }


def _resolved(index: int, *, winning: bool = True, pnl_cents: float = 75.0):
    return {
        "schema_version": "polyweather_weather_resolved_audit.v1",
        "fill_id": f"fill-{index}",
        "status": "resolved",
        "winning": winning,
        "pnl_cents": pnl_cents,
    }


def test_readiness_report_blocks_empty_journal_without_current_signal(tmp_path):
    report = build_live_readiness_report(journal_dir=tmp_path)

    assert report["live_gate"] is False
    assert report["live_authorization_pct"] == 0
    assert report["readiness_pct"] == 5.0
    assert "current_signal_report_missing" in report["blockers"]
    assert "insufficient_paper_fills_0_of_30" in report["blockers"]
    assert report["hard_conclusion"] == "只能继续 paper"


def test_readiness_report_surfaces_negative_markout_and_missing_resolution(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill(1)])
    _append_jsonl(tmp_path / "markouts.jsonl", [_markout(1, markout_cents=-0.1)])
    _append_jsonl(
        tmp_path / "resolved_audits.jsonl",
        [{"fill_id": "fill-1", "status": "unresolved"}],
    )

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=1),
    )

    assert report["signal"]["current_candidate_count"] == 1
    assert report["evidence"]["paper_fill_count"] == 1
    assert report["evidence"]["marked_count"] == 1
    assert report["evidence"]["mean_markout_cents"] == -0.1
    assert report["evidence"]["markout_win_rate"] == 0.0
    assert report["evidence"]["resolved_count"] == 0
    assert report["readiness_pct"] == 25.83
    assert report["live_gate"] is False
    assert "negative_mean_markout_cents" in report["blockers"]
    assert "insufficient_resolved_audits_0_of_10" in report["blockers"]


def test_readiness_report_surfaces_resolution_wait_state(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            _fill(
                1,
                market_id="future-market",
                market_slug="future-market",
                end_date="2026-06-28T12:00:00Z",
            )
        ],
    )
    _append_jsonl(tmp_path / "markouts.jsonl", [_markout(1, markout_cents=0.1)])
    _append_jsonl(
        tmp_path / "resolved_audits.jsonl",
        [{"fill_id": "fill-1", "status": "unresolved", "market_closed": False}],
    )

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=1),
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["evidence"]["resolved_gap_summary"]["hard_conclusion"] == "resolved_gap_wait_for_settlement"
    assert "resolved_audit_waiting_for_settlement" in report["blockers"]
    assert report["live_gate"] is False


def test_current_signal_report_overrides_historical_candidate_availability(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill(1)])

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
    )

    assert report["score_components"]["signal_availability"] == 0.0
    assert "no_current_weather_signal" in report["blockers"]


def test_readiness_report_attaches_signal_risk_filter_diagnostics(tmp_path):
    signal = _signal_report(candidate_count=0)
    signal["summary"].update(
        {
            "total_rows": 12,
            "quarantine_count": 2,
            "reject_count": 12,
            "quarantine_candidate_without_risk_count": 1,
            "quarantine_near_miss_count": 1,
            "quarantine_risk_only_count": 1,
        }
    )
    signal["rejection_reason_counts"] = [
        {"reason": "edge_below_min", "count": 8},
        {"reason": "negative_markout_rule:by_city:city=paris", "count": 2},
    ]
    signal["coverage_diagnostics"] = {
        "quarantine_by_reason": [
            {"quarantine_reason": "risk_rule_only_reject", "count": 1},
        ],
        "quarantine_by_blocker_scope": [
            {"quarantine_blocker_scope": "risk_rule_only", "count": 1},
        ],
        "decision_by_market_family": [
            {
                "market_family": "temperature",
                "candidate_count": 0,
                "quarantine_count": 2,
                "reject_count": 12,
            }
        ],
    }
    signal["source_diagnostics"] = {
        "model_join": {
            "joined": 12,
            "rows_seen": 12,
            "missing_scan_model_row": 0,
        }
    }
    signal["candidate_gap_report"] = {
        "risk_only_reject_count": 1,
        "near_candidates": [
            {
                "question": "Will Paris be hot?",
                "city": "paris",
                "bucket_type": "le",
                "bucket_label": "<= 37°C",
                "side": "no",
                "price": 0.19,
                "edge_percent": 12.1,
                "spread": 0.01,
                "risk_rule_hits": ["negative_markout_rule:by_city:city=paris"],
                "would_be_decision_without_risk_rules": "candidate",
            }
        ],
    }
    signal["risk_filter"] = {
        "enabled": True,
        "mode": "live",
        "source_rule_count": 4,
        "initial_rule_count": 4,
        "rule_count": 3,
        "hit_count": 10,
        "risk_rule_reject_count": 10,
        "risk_only_reject_count": 1,
        "pre_suppression_saturated_rule_count": 2,
        "suppressed_saturated_rule_count": 1,
        "suppressed_saturated_rules": [
            {
                "group": "by_market_family",
                "dimensions": {"market_family": "temperature"},
                "saturation_reason": "negative_markout_rule:by_market_family:market_family=temperature",
                "count": 16,
                "mean_markout_cents": -2.8,
                "win_rate": 0.2,
            }
        ],
        "suppressed_saturated_group_rule_count": 2,
        "suppressed_saturated_group_rules": [
            {
                "group": "by_side",
                "dimensions": {"side": "yes"},
                "saturation_group": "by_side",
                "saturation_group_coverage": 1.0,
                "saturation_group_reason_count": 2,
            }
        ],
        "saturated_rule_count": 1,
        "saturated_rules": [
            {
                "reason": "negative_markout_rule:by_bucket_type:bucket_type=eq",
                "scope": "medium",
                "count": 8,
                "total_rows": 10,
                "coverage": 0.8,
            }
        ],
        "saturated_group_count": 1,
        "saturated_groups": [
            {
                "group": "by_bucket_type",
                "scope": "medium",
                "count": 8,
                "total_rows": 10,
                "coverage": 0.8,
                "reason_count": 1,
            }
        ],
    }

    report = build_live_readiness_report(journal_dir=tmp_path, signal_report=signal)

    risk_summary = report["evidence"]["signal_risk_filter_summary"]
    current_signal = report["evidence"]["current_signal_diagnostics"]
    assert report["readiness_pct"] == 5.0
    assert current_signal["summary"]["quarantine_count"] == 2
    assert current_signal["top_rejection_reasons"][0]["reason"] == "edge_below_min"
    assert current_signal["quarantine_by_reason"][0]["quarantine_reason"] == "risk_rule_only_reject"
    assert current_signal["model_join"]["joined"] == 12
    assert current_signal["candidate_gap_counts"]["risk_only_reject_count"] == 1
    assert current_signal["near_candidates"][0]["city"] == "paris"
    assert risk_summary["suppressed_saturated_rule_count"] == 1
    assert risk_summary["suppressed_saturated_group_rule_count"] == 2
    assert risk_summary["saturated_rule_count"] == 1
    assert risk_summary["saturated_group_count"] == 1
    assert risk_summary["suppressed_saturated_rules"][0]["group"] == "by_market_family"
    assert risk_summary["suppressed_saturated_group_rules"][0]["group"] == "by_side"
    assert "risk_filter_suppressed_saturated_broad_rules" in report["diagnostic_blockers"]
    assert "risk_filter_suppressed_saturated_partition_rules" in report["diagnostic_blockers"]
    assert "risk_filter_saturated_rules_remaining" in report["diagnostic_blockers"]
    assert "risk_filter_saturated_groups_remaining" in report["diagnostic_blockers"]
    assert "current_signal_quarantine_only" in report["diagnostic_blockers"]
    assert "current_signal_risk_rules_block_candidate_like_rows" in report["diagnostic_blockers"]


def test_readiness_report_attaches_maker_quote_blocker_calibration(tmp_path):
    calibration = {
        "schema_version": "polyweather_weather_maker_quote_blocker_calibration.v1",
        "hard_conclusion": "maker_quote_specific_blockers_currently_negative",
        "paper_only": True,
        "counts_for_live_gate": False,
        "blocker_count": 1,
        "specific_candidate_blocker_count": 1,
        "eligible_blocker_count": 0,
        "keep_blocked_count": 1,
        "collect_more_count": 0,
        "blockers": [
            {
                "reason": "negative_markout_rule:maker_quote_by_time_to_expiry_and_spread:x=y",
                "group": "maker_quote_by_time_to_expiry_and_spread",
                "specificity": "specific",
                "dimensions": {"x": "y"},
                "current_hit_count": 1,
                "current_candidate_like_count": 1,
                "action": "keep_blocked_by_maker_quote_evidence",
                "failure_reasons": ["mean_maker_markout_below_threshold"],
                "evidence": {"count": 3, "mean_maker_markout_cents": -2.0},
            }
        ],
    }

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
        maker_quote_blocker_calibration_report=calibration,
    )

    summary = report["evidence"]["maker_quote_blocker_calibration_summary"]
    assert summary["hard_conclusion"] == "maker_quote_specific_blockers_currently_negative"
    assert summary["blockers"][0]["action"] == "keep_blocked_by_maker_quote_evidence"
    assert "maker_quote_specific_blockers_currently_negative" in report["diagnostic_blockers"]


def test_readiness_report_attaches_model_coverage_diagnostics(tmp_path):
    coverage = {
        "schema_version": "polyweather_weather_model_coverage.v1",
        "hard_conclusion": "non_temperature_model_gap_detected",
        "paper_only": True,
        "counts_for_live_gate": False,
        "total_rows": 2,
        "surface_ready_count": 2,
        "model_gap_surface_ready_count": 1,
        "model_build_queue_count": 1,
        "model_build_queue": [
            {
                "market_family": "rain",
                "model_gap_surface_ready_count": 1,
                "row_count": 1,
                "mean_model_gap_liquidity": 1200,
                "median_model_gap_spread": 0.01,
                "recommended_action": "build_precipitation_probability_model",
            }
        ],
    }

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
        model_coverage_report=coverage,
    )

    summary = report["evidence"]["model_coverage_summary"]
    assert summary["hard_conclusion"] == "non_temperature_model_gap_detected"
    assert summary["model_build_queue"][0]["market_family"] == "rain"
    assert "non_temperature_model_gap_detected" in report["diagnostic_blockers"]
    assert report["live_gate"] is False


def test_readiness_report_attaches_temperature_opportunity_diagnostics(tmp_path):
    opportunity = {
        "schema_version": "polyweather_weather_temperature_opportunity.v1",
        "hard_conclusion": "temperature_opportunity_blocked_by_negative_maker",
        "paper_only": True,
        "counts_for_live_gate": False,
        "scanned_temperature_count": 2,
        "excluded_bucket_count": 1,
        "blocked_by_negative_maker_count": 1,
        "eligible_for_formal_paper_count": 0,
        "collect_more_maker_evidence_count": 0,
        "blocked_by_negative_maker_evidence": [
            {
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "city": "paris",
                "side": "no",
                "bucket_type": "le",
                "price": 0.17,
                "spread": 0.01,
                "edge_percent": 14.1,
                "maker_hit_state": "negative",
                "maker_hit_details": [{"reason": "negative_markout_rule:maker_quote_by_x:x=y"}],
            }
        ],
    }

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
        temperature_opportunity_report=opportunity,
    )

    summary = report["evidence"]["temperature_opportunity_summary"]
    assert summary["hard_conclusion"] == "temperature_opportunity_blocked_by_negative_maker"
    assert summary["blocked_by_negative_maker_evidence"][0]["bucket_type"] == "le"
    assert "temperature_opportunity_blocked_by_negative_maker" in report["diagnostic_blockers"]
    assert report["live_gate"] is False


def test_readiness_report_attaches_temperature_execution_experiment_diagnostics(tmp_path):
    execution = {
        "schema_version": "polyweather_weather_temperature_execution_experiment.v1",
        "hard_conclusion": "temperature_execution_experiment_collect_offset_ladder",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "blocked_negative_maker_source_count": 1,
        "experiment_count": 1,
        "collect_offset_ladder_shadow_quote_count": 1,
        "experiments": [
            {
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "city": "paris",
                "side": "no",
                "bucket_type": "le",
                "price": 0.17,
                "spread": 0.01,
                "edge_percent": 14.1,
                "maker_evidence": {"mean_maker_markout_cents": -3.0},
                "taker_cross": {"proxy_mean_markout_cents": -0.25},
                "offsets_covering_adverse_count": 1,
                "next_action": "collect_offset_ladder_shadow_quotes",
            }
        ],
    }

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
        temperature_execution_experiment_report=execution,
    )

    summary = report["evidence"]["temperature_execution_experiment_summary"]
    assert summary["hard_conclusion"] == "temperature_execution_experiment_collect_offset_ladder"
    assert summary["experiments"][0]["next_action"] == "collect_offset_ladder_shadow_quotes"
    assert "temperature_execution_experiment_collect_offset_ladder" in report["diagnostic_blockers"]
    assert report["live_gate"] is False


def test_readiness_report_attaches_temperature_taker_validation_diagnostics(tmp_path):
    taker_validation = {
        "schema_version": "polyweather_weather_temperature_taker_paper_validation.v1",
        "hard_conclusion": "temperature_taker_validation_collect_more_markouts",
        "next_action": "continue_taker_paper_markouts",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "summary": {
            "paper_fill_count": 1,
            "marked_count": 1,
            "mean_markout_cents": 1.0,
            "win_rate": 1.0,
            "resolved_count": 0,
        },
        "horizon_status": [
            {
                "markout_horizon": "0-5m",
                "count": 1,
                "mean_markout_cents": 1.0,
                "win_rate": 1.0,
                "blockers": ["insufficient_horizon_count_1_of_3"],
            }
        ],
        "missing_horizons": ["5-15m", "15-30m"],
        "blockers": ["insufficient_marked_count_1_of_10"],
    }

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
        temperature_taker_validation_report=taker_validation,
    )

    summary = report["evidence"]["temperature_taker_validation_summary"]
    assert summary["hard_conclusion"] == "temperature_taker_validation_collect_more_markouts"
    assert summary["next_action"] == "continue_taker_paper_markouts"
    assert summary["summary"]["marked_count"] == 1
    assert "temperature_taker_validation_collect_more_markouts" in report["diagnostic_blockers"]
    assert report["live_gate"] is False


def test_readiness_report_attaches_current_signal_taker_validation_diagnostics(tmp_path):
    current_taker_validation = {
        "schema_version": "polyweather_weather_current_signal_taker_validation.v1",
        "hard_conclusion": "current_signal_taker_validation_negative",
        "next_action": "keep_current_signal_taker_blocked",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_gate": False,
        "summary": {
            "paper_fill_count": 20,
            "marked_count": 20,
            "mean_markout_cents": -1.55,
            "win_rate": 0.0,
            "resolved_count": 0,
        },
        "horizon_status": [
            {
                "markout_horizon": "0-5m",
                "count": 20,
                "mean_markout_cents": -1.55,
                "win_rate": 0.0,
                "blockers": ["horizon_mean_markout_below_threshold"],
            }
        ],
        "latest_strata_summary": {
            "by_quarantine_reason": [
                {
                    "quarantine_reason": "single_non_risk_blocker:bucket_type",
                    "count": 19,
                    "mean_markout_cents": -1.578947,
                }
            ]
        },
        "blockers": ["mean_markout_below_threshold", "win_rate_below_threshold"],
    }

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=0),
        current_signal_taker_validation_report=current_taker_validation,
    )

    summary = report["evidence"]["current_signal_taker_validation_summary"]
    assert summary["hard_conclusion"] == "current_signal_taker_validation_negative"
    assert summary["summary"]["mean_markout_cents"] == -1.55
    assert summary["latest_strata_summary"]["by_quarantine_reason"][0]["count"] == 19
    assert "current_signal_taker_validation_negative" in report["diagnostic_blockers"]
    assert report["live_gate"] is False


def test_load_signal_report_unwraps_full_cycle_payload(tmp_path):
    path = tmp_path / "cycle.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polyweather_weather_paper_cycle.v1",
                "signal_report": _signal_report(candidate_count=2),
            }
        ),
        encoding="utf-8",
    )

    report = load_signal_report(path)

    assert report is not None
    assert report["summary"]["candidate_count"] == 2


def test_readiness_report_excludes_quarantine_fills_from_live_gate(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [_fill(index, signal_bucket="quarantine", counts_for_live_gate=False) for index in range(30)],
    )
    _append_jsonl(tmp_path / "markouts.jsonl", [_markout(index) for index in range(30)])
    _append_jsonl(tmp_path / "resolved_audits.jsonl", [_resolved(index) for index in range(10)])

    report = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=1),
        live_permission=True,
    )

    assert report["evidence"]["paper_fill_count"] == 0
    assert report["evidence"]["marked_count"] == 0
    assert report["evidence"]["resolved_count"] == 0
    assert report["live_gate"] is False
    assert report["live_authorization_pct"] == 0
    assert "insufficient_paper_fills_0_of_30" in report["blockers"]


def test_readiness_report_attaches_quarantine_surface_as_diagnostic_only(tmp_path):
    journal_dir = tmp_path / "formal"
    quarantine_dir = tmp_path / "quarantine"
    _append_jsonl(
        quarantine_dir / "paper_fills.jsonl",
        [_fill(1, signal_bucket="quarantine", counts_for_live_gate=False, city="seoul")],
    )
    _append_jsonl(quarantine_dir / "markouts.jsonl", [_markout(1, markout_cents=-0.4)])

    report = build_live_readiness_report(
        journal_dir=journal_dir,
        quarantine_journal_dir=quarantine_dir,
        signal_report=_signal_report(candidate_count=0),
        include_quarantine_surface=True,
        quarantine_surface_min_decision_count=1,
        quarantine_surface_min_promote_count=1,
    )

    assert report["readiness_pct"] == 5.0
    assert report["live_gate"] is False
    assert report["evidence"]["paper_fill_count"] == 0
    surface = report["evidence"]["quarantine_surface_summary"]
    assert surface["hard_conclusion"] == "quarantine_surface_currently_negative"
    assert surface["counts_for_live_gate"] is False
    assert "quarantine_surface_currently_negative" in report["diagnostic_blockers"]


def test_readiness_report_reaches_full_gate_only_with_evidence_and_permission(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill(index) for index in range(30)])
    _append_jsonl(tmp_path / "markouts.jsonl", [_markout(index) for index in range(30)])
    _append_jsonl(tmp_path / "resolved_audits.jsonl", [_resolved(index) for index in range(10)])

    without_permission = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=1),
    )
    with_permission = build_live_readiness_report(
        journal_dir=tmp_path,
        signal_report=_signal_report(candidate_count=1),
        live_permission=True,
    )

    assert without_permission["live_gate"] is True
    assert without_permission["live_authorization_pct"] == 0
    assert without_permission["readiness_pct"] == 95.0
    assert without_permission["hard_conclusion"] == "只能继续 paper"
    assert with_permission["live_gate"] is True
    assert with_permission["live_authorization_pct"] == 100
    assert with_permission["readiness_pct"] == 100.0
    assert with_permission["blockers"] == []
    assert with_permission["hard_conclusion"] == "可实盘"
