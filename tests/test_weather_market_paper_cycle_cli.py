from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import weather_market_paper_cycle as cycle_cli


def _subjournal(root: str, name: str) -> str:
    return str(Path(root) / "subjournals" / name)


@pytest.fixture(autouse=True)
def _paper_cycle_orderbook_archive_is_paper_only(monkeypatch):
    def fake_archive(payload, **kwargs):
        return {
            "schema_version": "polyweather_polymarket_orderbook_archive.v1",
            "paper_only": True,
            "counts_for_live_gate": False,
            "rows_seen": len(payload.get("rows") or []),
            "snapshot_count": 0,
            "written_count": 0,
            "archive_dir": str(kwargs.get("archive_dir")),
        }

    monkeypatch.setattr(cycle_cli, "write_orderbook_archive_from_payload", fake_archive)


def test_weather_market_paper_cycle_runs_paper_only_cycle(monkeypatch, tmp_path, capsys):
    calls = {}

    def fake_polymarket(**kwargs):
        calls["polymarket"] = kwargs
        return {"rows": []}

    def fake_scan(filters, force_refresh=False):
        calls["scan"] = {"filters": filters, "force_refresh": force_refresh}
        return {"rows": []}

    monkeypatch.setattr(cycle_cli, "build_polymarket_weather_payload", fake_polymarket)
    monkeypatch.setattr(cycle_cli, "build_scan_terminal_payload", fake_scan)
    monkeypatch.setattr(cycle_cli, "temperature_model_targets_from_payload", lambda payload: {})
    monkeypatch.setattr(
        cycle_cli,
        "enrich_polymarket_payload_with_scan_models",
        lambda payload, scan_payload: payload,
    )
    def fake_signal_report(payload, config, risk_rules=None, risk_rule_mode="live", generated_at=None):
        calls["signal_config"] = config
        return {
            "summary": {"candidate_count": 1, "live_gate": False},
            "source_diagnostics": {"model_join": {"joined": 1}},
        }

    monkeypatch.setattr(cycle_cli, "build_weather_market_signal_report", fake_signal_report)
    def fake_risk_rules(**kwargs):
        calls["risk_rules_build"] = kwargs
        return {
            "rule_count": 2,
            "rules": [
                {
                    "action": "do_not_live_until_positive_markout",
                    "dimensions": {"city": "ankara"},
                }
            ],
        }

    monkeypatch.setattr(cycle_cli, "build_risk_rules_from_journal", fake_risk_rules)
    def fake_journal(*args, **kwargs):
        calls["journal"] = kwargs
        return {"fill_count": 1}

    def fake_markout(**kwargs):
        calls["markout"] = kwargs
        return {"marked_count": 1, "records": [{"private": "omitted"}]}

    def fake_audit(**kwargs):
        calls["audit"] = kwargs
        return {"resolved_count": 0, "records": [{"private": "omitted"}]}

    def fake_readiness(**kwargs):
        calls["readiness"] = kwargs
        return {"readiness_pct": 31.67, "live_gate": False}

    def fake_gap(**kwargs):
        calls["gap"] = kwargs
        return {"hard_conclusion": "resolved_gap_wait_for_settlement", "rows": [{"private": "omitted"}]}

    def fake_calibration(**kwargs):
        calls["calibration"] = kwargs
        return {
            "hard_conclusion": "no_threshold_profile_passed_evidence_gate",
            "top_profiles": [{"private": "omitted"}],
        }

    def fake_promotion(**kwargs):
        calls["promotion"] = kwargs
        return {
            "hard_conclusion": "no_quarantine_group_ready_for_formal_paper",
            "groups": {"private": "omitted"},
            "promotion_group_count": 0,
        }

    monkeypatch.setattr(cycle_cli, "write_paper_journal", fake_journal)
    monkeypatch.setattr(cycle_cli, "markout_open_paper_fills", fake_markout)
    monkeypatch.setattr(cycle_cli, "audit_paper_fills_resolution", fake_audit)
    monkeypatch.setattr(cycle_cli, "build_resolved_gap_report", fake_gap)
    monkeypatch.setattr(cycle_cli, "build_quality_threshold_calibration_report", fake_calibration)
    monkeypatch.setattr(cycle_cli, "build_quarantine_promotion_report", fake_promotion)
    monkeypatch.setattr(cycle_cli, "build_live_readiness_report", fake_readiness)

    cycle_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/weather-paper",
            "--backfill-dir",
            "/tmp/weather-backfill",
            "--paper-max-fills",
            "3",
            "--markout-max-fills",
            "4",
            "--audit-max-fills",
            "5",
            "--apply-markout-risk-rules",
            "--risk-rule-min-count",
            "4",
            "--risk-rule-mode",
            "exploration",
            "--quality-surface-profile",
            "--allowed-side",
            "no",
            "--write-current-signal-report",
            "--current-signal-report-dir",
            str(tmp_path / "signals"),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["paper_only"] is True
    assert output["live_order_path"] is False
    assert output["orderbook_archive"]["paper_only"] is True
    assert output["orderbook_archive"]["counts_for_live_gate"] is False
    assert output["orderbook_archive_coverage"]["schema_version"] == (
        "polyweather_weather_orderbook_archive_coverage.v1"
    )
    assert output["orderbook_archive_coverage"]["counts_for_live_gate"] is False
    assert output["signal_summary"]["candidate_count"] == 1
    assert output["markout"] == {"marked_count": 1}
    assert output["resolved_audit"] == {"resolved_count": 0}
    assert output["resolved_gap_report"] == {"hard_conclusion": "resolved_gap_wait_for_settlement"}
    assert output["quality_threshold_calibration"] == {
        "hard_conclusion": "no_threshold_profile_passed_evidence_gate"
    }
    assert output["quarantine_promotion"] == {
        "hard_conclusion": "no_quarantine_group_ready_for_formal_paper",
        "promotion_group_count": 0,
    }
    assert output["live_readiness_progress"]["readiness_pct"] == 31.67
    assert output["model_coverage"]["schema_version"] == "polyweather_weather_model_coverage.v1"
    assert output["model_coverage"]["counts_for_live_gate"] is False
    assert output["temperature_opportunity"]["schema_version"] == "polyweather_weather_temperature_opportunity.v1"
    assert output["temperature_opportunity"]["counts_for_live_gate"] is False
    assert output["temperature_execution_experiment"]["schema_version"] == (
        "polyweather_weather_temperature_execution_experiment.v1"
    )
    assert output["temperature_execution_experiment"]["counts_for_live_gate"] is False
    assert output["current_signal_snapshot"]["paper_only"] is True
    assert output["current_signal_snapshot"]["candidate_count"] == 1
    assert output["effective_profile"]["current_signal_report_enabled"] is True
    assert output["effective_profile"]["current_signal_report_dir"] == str(tmp_path / "signals")
    assert output["effective_profile"]["model_coverage_max_horizon_days"] == 14.0
    assert output["effective_profile"]["model_coverage_thresholds"]["min_price"] == 0.03
    assert output["effective_profile"]["temperature_opportunity_max_horizon_hours"] == 48.0
    assert output["effective_profile"]["temperature_execution_offset_cents"] == [0.0, 0.5, 1.0, 2.0, 3.0]
    assert output["risk_rules"]["rule_count"] == 2
    assert output["effective_profile"]["risk_rule_min_count"] == 4
    assert output["effective_profile"]["quarantine_surface_risk_rules_enabled"] is False
    assert output["effective_profile"]["suppress_saturated_broad_risk_rules"] is False
    assert calls["risk_rules_build"]["journal_dir"] == "/tmp/weather-paper"
    assert calls["risk_rules_build"]["min_count"] == 4
    assert calls["risk_rules_build"]["include_quarantine_surface_rules"] is False
    assert calls["polymarket"]["exclude_expired_markets"] is True
    assert calls["polymarket"]["exclude_not_accepting_orders"] is True
    assert calls["journal"]["max_fills"] == 3
    assert calls["markout"]["max_fills"] == 4
    assert calls["audit"]["max_fills"] == 5
    assert calls["audit"]["backfill_dir"] == "/tmp/weather-backfill"
    assert calls["gap"]["journal_dir"] == "/tmp/weather-paper"
    assert calls["gap"]["backfill_dir"] == "/tmp/weather-backfill"
    assert calls["calibration"]["paper_journal_dir"] == "/tmp/weather-paper"
    assert calls["calibration"]["quarantine_journal_dir"] == _subjournal("/tmp/weather-paper", "quarantine")
    assert calls["promotion"]["paper_journal_dir"] == "/tmp/weather-paper"
    assert calls["promotion"]["signal_report"]["summary"]["candidate_count"] == 1
    assert calls["readiness"]["live_permission"] is False
    assert calls["readiness"]["temperature_opportunity_report"]["counts_for_live_gate"] is False
    assert calls["readiness"]["temperature_execution_experiment_report"]["counts_for_live_gate"] is False
    assert calls["readiness"]["orderbook_archive_coverage_report"]["counts_for_live_gate"] is False
    assert calls["readiness"]["backfill_dir"] == "/tmp/weather-backfill"
    assert calls["signal_config"].min_price == 0.03
    assert calls["signal_config"].min_liquidity == 10.0
    assert calls["signal_config"].max_spread == 0.02
    assert calls["signal_config"].require_order_book is True
    assert calls["signal_config"].allowed_sides == ("no",)
    assert calls["signal_config"].excluded_bucket_types == ("eq",)
    assert calls["signal_config"].min_bid_depth_usdc_3c == 10.0
    assert calls["signal_config"].min_ask_depth_usdc_3c == 10.0
    assert calls["signal_config"].suppress_saturated_broad_risk_rules is False
    assert calls["signal_config"].suppress_saturated_partition_risk_rules is False


def test_weather_market_paper_cycle_live_permission_keeps_order_path_disabled(monkeypatch, capsys):
    calls = {}

    monkeypatch.setattr(cycle_cli, "build_polymarket_weather_payload", lambda **kwargs: {"rows": []})
    monkeypatch.setattr(cycle_cli, "build_scan_terminal_payload", lambda filters, force_refresh=False: {"rows": []})
    monkeypatch.setattr(cycle_cli, "temperature_model_targets_from_payload", lambda payload: {})
    monkeypatch.setattr(cycle_cli, "enrich_polymarket_payload_with_scan_models", lambda payload, scan_payload: payload)
    monkeypatch.setattr(
        cycle_cli,
        "build_weather_market_signal_report",
        lambda payload, config, risk_rules=None, risk_rule_mode="live", generated_at=None: {
            "summary": {"candidate_count": 0, "live_gate": False}
        },
    )
    monkeypatch.setattr(cycle_cli, "build_risk_rules_from_journal", lambda **kwargs: {"rule_count": 0, "rules": []})
    monkeypatch.setattr(cycle_cli, "write_paper_journal", lambda *args, **kwargs: {"fill_count": 0})
    monkeypatch.setattr(cycle_cli, "markout_open_paper_fills", lambda **kwargs: {"marked_count": 0})
    monkeypatch.setattr(cycle_cli, "audit_paper_fills_resolution", lambda **kwargs: {"resolved_count": 0})
    monkeypatch.setattr(cycle_cli, "build_resolved_gap_report", lambda **kwargs: {"hard_conclusion": "no_open_fills"})
    monkeypatch.setattr(
        cycle_cli,
        "build_quality_threshold_calibration_report",
        lambda **kwargs: {"hard_conclusion": "no_threshold_profile_passed_evidence_gate"},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_quarantine_promotion_report",
        lambda **kwargs: {"hard_conclusion": "no_quarantine_group_ready_for_formal_paper"},
    )

    def fake_readiness(**kwargs):
        calls["readiness"] = kwargs
        return {"readiness_pct": 100.0, "live_gate": True, "live_authorization_pct": 100}

    monkeypatch.setattr(cycle_cli, "build_live_readiness_report", fake_readiness)

    cycle_cli.main(["--live-permission"])

    output = json.loads(capsys.readouterr().out)
    assert calls["readiness"]["live_permission"] is True
    assert output["paper_only"] is True
    assert output["live_order_path"] is False
    assert output["live_readiness_progress"]["live_gate"] is True
    assert output["live_readiness_progress"]["live_authorization_pct"] == 100


def test_weather_market_paper_cycle_can_write_separate_quarantine_journal(monkeypatch, capsys):
    calls = {"journals": [], "markouts": [], "audits": [], "maker_writes": [], "maker_markouts": []}

    monkeypatch.setattr(cycle_cli, "build_polymarket_weather_payload", lambda **kwargs: {"rows": []})
    monkeypatch.setattr(cycle_cli, "build_scan_terminal_payload", lambda filters, force_refresh=False: {"rows": []})
    monkeypatch.setattr(cycle_cli, "temperature_model_targets_from_payload", lambda payload: {"seoul": ["2026-06-27"]})
    monkeypatch.setenv("POLYWEATHER_COLLECTOR_PATCH_ENDPOINT", "http://example.test/api/internal/collector-patch")

    def fake_analysis_fallback(targets, force_refresh=False, detail_mode="panel"):
        calls["collector_patch_endpoint_during_fallback"] = os.environ.get(
            "POLYWEATHER_COLLECTOR_PATCH_ENDPOINT"
        )
        return {
            "snapshot_id": "fallback",
            "status": "ready",
            "rows": [],
            "diagnostics": {"target_city_count": len(targets)},
        }

    monkeypatch.setattr(cycle_cli, "build_analysis_model_payload_for_targets", fake_analysis_fallback)
    monkeypatch.setattr(cycle_cli, "enrich_polymarket_payload_with_scan_models", lambda payload, scan_payload: payload)
    monkeypatch.setattr(
        cycle_cli,
        "build_weather_market_signal_report",
        lambda payload, config, risk_rules=None, risk_rule_mode="live", generated_at=None: {
            "summary": {"candidate_count": 0, "quarantine_count": 1, "live_gate": False},
            "quarantine": [{"decision": "quarantine", "token_id": "q-token"}],
        },
    )
    monkeypatch.setattr(cycle_cli, "build_risk_rules_from_journal", lambda **kwargs: {"rule_count": 0, "rules": []})

    def fake_journal(*args, **kwargs):
        calls["journals"].append(kwargs)
        return {"fill_count": 1, "journal_dir": kwargs["journal_dir"]}

    def fake_markout(**kwargs):
        calls["markouts"].append(kwargs)
        return {"marked_count": 1, "records": []}

    def fake_audit(**kwargs):
        calls["audits"].append(kwargs)
        return {"resolved_count": 0, "records": []}

    monkeypatch.setattr(cycle_cli, "write_paper_journal", fake_journal)
    monkeypatch.setattr(cycle_cli, "markout_open_paper_fills", fake_markout)
    monkeypatch.setattr(cycle_cli, "audit_paper_fills_resolution", fake_audit)
    monkeypatch.setattr(
        cycle_cli,
        "build_resolved_gap_report",
        lambda **kwargs: {"hard_conclusion": "resolved_gap_wait_for_settlement", "rows": []},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_quality_threshold_calibration_report",
        lambda **kwargs: {"hard_conclusion": "no_threshold_profile_passed_evidence_gate", "top_profiles": []},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_quarantine_promotion_report",
        lambda **kwargs: {"hard_conclusion": "no_quarantine_group_ready_for_formal_paper", "groups": []},
    )
    monkeypatch.setattr(cycle_cli, "build_live_readiness_report", lambda **kwargs: {"readiness_pct": 20.83, "live_gate": False})
    monkeypatch.setattr(
        cycle_cli,
        "write_maker_quote_journal_from_fills",
        lambda **kwargs: calls["maker_writes"].append(kwargs) or {"quote_records_written": 1, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "markout_open_maker_quotes",
        lambda **kwargs: calls["maker_markouts"].append(kwargs) or {"inferred_fill_count": 0, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "summarize_maker_quote_journal",
        lambda journal_dir: {"quote_count": 1, "journal_dir": journal_dir},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_quarantine_validation_report",
        lambda **kwargs: {"unlock_candidate_count": 0, "paper_journal_dir": kwargs["paper_journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_quarantine_surface_report",
        lambda **kwargs: {
            "hard_conclusion": "quarantine_surface_collect_more",
            "groups": [{"private": "omitted"}],
        },
    )

    cycle_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/weather-paper",
            "--quarantine-journal-dir",
            "/tmp/weather-quarantine",
            "--write-quarantine-journal",
            "--quarantine-max-fills",
            "7",
            "--write-maker-quotes",
            "--maker-quote-offset-cents",
            "0",
            "--maker-quote-offset-cents",
            "1",
            "--maker-max-quotes",
            "11",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert len(calls["journals"]) == 2
    assert calls["journals"][0]["journal_dir"] == "/tmp/weather-paper"
    assert calls["journals"][0].get("include_quarantine") is not True
    assert calls["journals"][1]["journal_dir"] == "/tmp/weather-quarantine"
    assert calls["journals"][1]["include_candidates"] is False
    assert calls["journals"][1]["include_quarantine"] is True
    assert calls["journals"][1]["max_fills"] == 7
    assert len(calls["maker_writes"]) == 2
    assert calls["maker_writes"][0]["journal_dir"] == "/tmp/weather-paper"
    assert calls["maker_writes"][0]["quote_offset_cents"] == [0.0, 1.0]
    assert calls["maker_writes"][1]["journal_dir"] == "/tmp/weather-quarantine"
    assert calls["maker_writes"][1]["max_quotes"] == 11
    assert calls["maker_writes"][1]["quote_offset_cents"] == [0.0, 1.0]
    assert len(calls["maker_markouts"]) == 2
    assert calls["maker_markouts"][0]["min_markout_interval_seconds"] == 0.0
    assert calls["maker_markouts"][1]["min_markout_interval_seconds"] == 0.0
    assert output["quarantine_journal"]["journal_dir"] == "/tmp/weather-quarantine"
    assert output["quarantine_markout"] == {"marked_count": 1}
    assert output["quarantine_resolved_audit"] == {"resolved_count": 0}
    assert output["quarantine_maker_quote_summary"]["quote_count"] == 1
    assert output["quarantine_validation"]["unlock_candidate_count"] == 0
    assert output["quarantine_surface"] == {"hard_conclusion": "quarantine_surface_collect_more"}
    assert output["quarantine_promotion"]["hard_conclusion"] == "no_quarantine_group_ready_for_formal_paper"
    assert output["effective_profile"]["maker_quote_offset_cents"] == [0.0, 1.0]


def test_weather_market_paper_cycle_production_profile_sets_live_like_defaults(monkeypatch, tmp_path, capsys):
    calls = {
        "journals": [],
        "markouts": [],
        "audits": [],
        "temperature_execution_writes": [],
        "temperature_taker_runs": [],
        "maker_markouts": [],
    }

    def fake_polymarket(**kwargs):
        calls["polymarket"] = kwargs
        return {"rows": []}

    monkeypatch.setattr(
        cycle_cli,
        "build_polymarket_weather_payload",
        fake_polymarket,
    )
    monkeypatch.setattr(cycle_cli, "build_scan_terminal_payload", lambda filters, force_refresh=False: {"rows": []})
    monkeypatch.setattr(cycle_cli, "temperature_model_targets_from_payload", lambda payload: {"seoul": ["2026-06-27"]})
    monkeypatch.setenv("POLYWEATHER_COLLECTOR_PATCH_ENDPOINT", "http://example.test/api/internal/collector-patch")

    def fake_analysis_fallback(targets, force_refresh=False, detail_mode="panel"):
        calls["collector_patch_endpoint_during_fallback"] = os.environ.get(
            "POLYWEATHER_COLLECTOR_PATCH_ENDPOINT"
        )
        return {
            "snapshot_id": "fallback",
            "status": "ready",
            "rows": [],
            "diagnostics": {"target_city_count": len(targets)},
        }

    monkeypatch.setattr(cycle_cli, "build_analysis_model_payload_for_targets", fake_analysis_fallback)
    monkeypatch.setattr(cycle_cli, "enrich_polymarket_payload_with_scan_models", lambda payload, scan_payload: payload)

    def fake_signal_report(payload, config, risk_rules=None, risk_rule_mode="live", generated_at=None):
        calls["signal_config"] = config
        calls["risk_rules"] = risk_rules
        calls["risk_rule_mode"] = risk_rule_mode
        return {"summary": {"candidate_count": 0, "live_gate": False}}

    monkeypatch.setattr(cycle_cli, "build_weather_market_signal_report", fake_signal_report)
    def fake_risk_rules(**kwargs):
        calls["risk_rules_build"] = kwargs
        return {"rule_count": 1, "rules": [{"action": "do_not_live_until_positive_markout"}]}

    monkeypatch.setattr(cycle_cli, "build_risk_rules_from_journal", fake_risk_rules)
    monkeypatch.setattr(
        cycle_cli,
        "write_paper_journal",
        lambda *args, **kwargs: calls["journals"].append(kwargs) or {"fill_count": 0, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "markout_open_paper_fills",
        lambda **kwargs: calls["markouts"].append(kwargs) or {"records": [], "marked_count": 0, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "audit_paper_fills_resolution",
        lambda **kwargs: calls["audits"].append(kwargs) or {"records": [], "resolved_count": 0, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(cycle_cli, "build_resolved_gap_report", lambda **kwargs: {"rows": [], "hard_conclusion": "resolved_gap_wait_for_settlement"})
    monkeypatch.setattr(cycle_cli, "build_quality_threshold_calibration_report", lambda **kwargs: {"top_profiles": [], "hard_conclusion": "no_threshold_profile_passed_evidence_gate"})
    monkeypatch.setattr(cycle_cli, "build_quarantine_promotion_report", lambda **kwargs: {"groups": [], "hard_conclusion": "no_quarantine_group_ready_for_formal_paper"})
    monkeypatch.setattr(
        cycle_cli,
        "write_temperature_execution_quotes",
        lambda *args, **kwargs: calls["temperature_execution_writes"].append(kwargs)
        or {"quote_records_written": 0, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "markout_open_maker_quotes",
        lambda **kwargs: calls["maker_markouts"].append(kwargs)
        or {"inferred_fill_count": 0, "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "summarize_maker_quote_journal",
        lambda journal_dir: {"quote_count": 0, "journal_dir": journal_dir},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_quarantine_surface_report",
        lambda **kwargs: {
            "hard_conclusion": "quarantine_surface_currently_negative",
            "groups": [{"private": "omitted"}],
        },
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_targeted_shadow_validation_report",
        lambda **kwargs: {"hard_conclusion": "targeted_shadow_collect_more_or_reject", "journal_dir": kwargs["journal_dir"]},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_current_signal_taker_validation_report",
        lambda **kwargs: {
            "hard_conclusion": "current_signal_taker_validation_collect_more_markouts",
            "journal_dir": kwargs["journal_dir"],
            "counts_for_live_gate": False,
        },
    )
    monkeypatch.setattr(
        cycle_cli,
        "summarize_execution_calibration",
        lambda *args, **kwargs: {
            "fill_count": 3,
            "execution_delta": {"maker_bid_mean_markout_cents": 0.2},
            "by_execution_and_spread": [],
        },
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_maker_focus_signal_report",
        lambda *args, **kwargs: {
            "summary": {
                "maker_focus_count": 0,
                "maker_focus_suppressed_count": 0,
                "live_gate": False,
            },
            "selection_config": {"group_fields": ["entry_spread_bucket"]},
            "eligible_group_count": 0,
            "eligible_groups": [],
            "hard_conclusion": "maker_focus_no_positive_historical_stratum",
        },
    )
    monkeypatch.setattr(
        cycle_cli,
        "run_temperature_taker_paper_cycle",
        lambda **kwargs: calls["temperature_taker_runs"].append(kwargs)
        or {
            "paper_only": True,
            "counts_for_live_gate": False,
            "taker_validation": {
                "hard_conclusion": "temperature_taker_validation_collect_more_markouts",
                "counts_for_live_gate": False,
            },
        },
    )

    def fake_readiness(**kwargs):
        calls["readiness"] = kwargs
        return {"readiness_pct": 20.33, "live_gate": False}

    monkeypatch.setattr(cycle_cli, "build_live_readiness_report", fake_readiness)

    cycle_cli.main(
        [
            "--production-profile",
            "--paper-journal-dir",
            "/tmp/weather-paper",
            "--current-signal-report-dir",
            str(tmp_path / "production-signals"),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["production_profile"] is True
    assert output["effective_profile"]["polymarket_queries"] == ["temperature"]
    assert output["effective_profile"]["polymarket_row_limit"] == 240
    assert output["effective_profile"]["polymarket_active_scan_limit"] == 500
    assert output["effective_profile"]["max_city_temperature_queries"] == 6
    assert output["effective_profile"]["quality_surface_profile"] is True
    assert output["effective_profile"]["apply_markout_risk_rules"] is True
    assert output["effective_profile"]["risk_rule_min_count"] == 1
    assert output["effective_profile"]["suppress_saturated_broad_risk_rules"] is True
    assert output["effective_profile"]["suppress_saturated_partition_risk_rules"] is True
    assert output["effective_profile"]["saturated_risk_rule_min_coverage"] == 0.8
    assert output["effective_profile"]["partition_saturated_risk_rule_min_coverage"] == 0.8
    assert output["effective_profile"]["collector_patch_enabled"] is False
    assert output["effective_profile"]["current_signal_report_enabled"] is True
    assert output["current_signal_snapshot"]["paper_only"] is True
    assert output["current_signal_snapshot"]["candidate_count"] == 0
    assert output["effective_profile"]["strict_gate_queue_enabled"] is True
    assert output["effective_profile"]["strict_gate_queue_dir"] == "/tmp/weather-paper/strict_gate_queues"
    assert output["effective_profile"]["strict_gate_queue_max_dust_records_per_queue"] == 2
    assert output["live_evidence_bundle_hint"]["schema_version"] == (
        "polyweather_weather_live_evidence_bundle_hint.v1"
    )
    assert output["live_evidence_bundle_hint"]["paper_journal_dir"] == "/tmp/weather-paper"
    assert output["live_evidence_bundle_hint"]["strict_gate_queue_dir"] == (
        "/tmp/weather-paper/strict_gate_queues"
    )
    assert output["live_evidence_bundle_hint"]["orderbook_archive_dir"] == (
        "data/trading/polymarket_orderbooks"
    )
    assert "--strict-gate-queue-dir" in output["live_evidence_bundle_hint"]["recommended_command"]
    assert output["effective_profile"]["live_evidence_bundle_hint"] == output["live_evidence_bundle_hint"]
    assert output["effective_profile"]["targeted_shadow_enabled"] is True
    assert output["effective_profile"]["targeted_shadow_quarantine_reasons"] == ["risk_rule_only_reject"]
    assert output["effective_profile"]["targeted_shadow_bucket_types"] == ["le"]
    assert output["effective_profile"]["targeted_shadow_near_miss_categories"] == ["price", "depth"]
    assert output["effective_profile"]["targeted_shadow_min_edge_percent"] == 5.0
    assert output["effective_profile"]["targeted_shadow_cooldown_enabled"] is True
    assert output["effective_profile"]["targeted_shadow_cooldown_min_marked_count"] == 2
    assert output["effective_profile"]["current_signal_taker_enabled"] is True
    assert output["effective_profile"]["current_signal_taker_journal_dir"] == _subjournal(
        "/tmp/weather-paper", "current_signal_taker"
    )
    assert output["effective_profile"]["current_signal_taker_min_edge_percent"] == 5.0
    assert output["effective_profile"]["current_signal_taker_max_spread"] == 0.03
    assert output["effective_profile"]["current_signal_taker_allowed_bucket_types"] == ["le", "ge"]
    assert output["effective_profile"]["current_signal_taker_validation_required_horizons"] == [
        "0-5m",
        "5-15m",
        "15-30m",
    ]
    assert output["effective_profile"]["eq_shadow_enabled"] is True
    assert output["effective_profile"]["eq_shadow_journal_dir"] == _subjournal("/tmp/weather-paper", "eq_shadow")
    assert output["effective_profile"]["eq_shadow_bucket_types"] == ["eq"]
    assert output["effective_profile"]["eq_shadow_near_miss_categories"] == ["bucket_type"]
    assert output["effective_profile"]["eq_shadow_min_edge_percent"] == 5.0
    assert output["effective_profile"]["eq_shadow_require_edge_floor_for_direct_reasons"] is True
    assert output["effective_profile"]["eq_shadow_cooldown_enabled"] is True
    assert output["effective_profile"]["maker_focus_enabled"] is True
    assert output["effective_profile"]["maker_focus_journal_dir"] == _subjournal("/tmp/weather-paper", "maker_focus")
    assert output["effective_profile"]["maker_focus_group_fields"] == ["entry_spread_bucket"]
    assert output["effective_profile"]["maker_focus_min_group_count"] == 3
    assert output["effective_profile"]["maker_focus_respect_maker_quote_risk_rules"] is True
    assert output["effective_profile"]["temperature_execution_quotes_enabled"] is True
    assert output["effective_profile"]["temperature_execution_journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_execution"
    )
    assert output["effective_profile"]["temperature_taker_paper_enabled"] is True
    assert output["effective_profile"]["temperature_taker_journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_taker"
    )
    assert output["effective_profile"]["temperature_taker_validation_required_horizons"] == [
        "0-5m",
        "5-15m",
        "15-30m",
    ]
    assert output["effective_profile"]["quarantine_journal_enabled"] is True
    assert output["effective_profile"]["quarantine_journal_dir"] == _subjournal("/tmp/weather-paper", "quarantine")
    assert output["effective_profile"]["quarantine_surface_min_decision_count"] == 5
    assert output["effective_profile"]["quarantine_surface_min_promote_count"] == 5
    assert output["effective_profile"]["quarantine_surface_risk_rules_enabled"] is True
    assert output["targeted_shadow"]["selection_cooldown"]["hard_conclusion"] == "targeted_shadow_no_cooldown"
    assert output["targeted_shadow"]["counts_for_live_gate"] is False
    assert output["targeted_shadow"]["journal"]["journal_dir"] == _subjournal(
        "/tmp/weather-paper", "targeted_shadow"
    )
    assert output["targeted_shadow"]["validation"]["hard_conclusion"] == "targeted_shadow_collect_more_or_reject"
    assert output["current_signal_taker"]["counts_for_live_gate"] is False
    assert output["current_signal_taker"]["journal"]["journal_dir"] == _subjournal(
        "/tmp/weather-paper", "current_signal_taker"
    )
    assert output["current_signal_taker"]["validation"]["hard_conclusion"] == (
        "current_signal_taker_validation_collect_more_markouts"
    )
    assert output["eq_shadow"]["selection_cooldown"]["hard_conclusion"] == "targeted_shadow_no_cooldown"
    assert output["eq_shadow"]["target_config"]["bucket_types"] == ["eq"]
    assert output["eq_shadow"]["target_config"]["require_edge_floor_for_direct_reasons"] is True
    assert output["eq_shadow"]["journal"]["journal_dir"] == _subjournal("/tmp/weather-paper", "eq_shadow")
    assert output["eq_shadow"]["counts_for_live_gate"] is False
    assert output["eq_shadow"]["paper_only"] is True
    assert output["maker_focus"]["hard_conclusion"] == "maker_focus_no_positive_historical_stratum"
    assert output["maker_focus"]["journal"]["journal_dir"] == _subjournal("/tmp/weather-paper", "maker_focus")
    assert output["maker_focus"]["counts_for_live_gate"] is False
    assert output["maker_quote_blocker_calibration"]["hard_conclusion"] == "maker_quote_no_current_blockers"
    assert output["maker_quote_blocker_calibration"]["counts_for_live_gate"] is False
    assert output["temperature_execution_shadow"]["paper_only"] is True
    assert output["temperature_execution_shadow"]["journal"]["journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_execution"
    )
    assert output["temperature_execution_shadow"]["summary"]["quote_count"] == 0
    assert output["temperature_taker_paper"]["taker_validation"]["hard_conclusion"] == (
        "temperature_taker_validation_collect_more_markouts"
    )
    assert output["quarantine_journal"]["journal_dir"] == _subjournal("/tmp/weather-paper", "quarantine")
    assert output["quarantine_surface"] == {"hard_conclusion": "quarantine_surface_currently_negative"}
    assert output["effective_profile"]["max_quarantine"] == 30
    assert output["effective_profile"]["quarantine_near_miss_categories"] == [
        "edge",
        "spread",
        "price",
        "bucket_type",
        "liquidity",
        "depth",
    ]
    assert calls["polymarket"]["queries"] == ("temperature",)
    assert calls["polymarket"]["row_limit"] == 240
    assert calls["polymarket"]["active_scan_limit"] == 500
    assert calls["polymarket"]["max_city_temperature_queries"] == 6
    assert calls["collector_patch_endpoint_during_fallback"] == ""
    assert os.environ["POLYWEATHER_COLLECTOR_PATCH_ENDPOINT"] == "http://example.test/api/internal/collector-patch"
    assert calls["signal_config"].min_price == 0.03
    assert calls["signal_config"].min_liquidity == 10.0
    assert calls["signal_config"].max_spread == 0.02
    assert calls["signal_config"].require_order_book is True
    assert calls["signal_config"].excluded_bucket_types == ("eq",)
    assert calls["signal_config"].suppress_saturated_broad_risk_rules is True
    assert calls["signal_config"].suppress_saturated_partition_risk_rules is True
    assert calls["signal_config"].saturated_risk_rule_min_coverage == 0.8
    assert calls["signal_config"].partition_saturated_risk_rule_min_coverage == 0.8
    assert calls["signal_config"].max_quarantine == 30
    assert calls["signal_config"].quarantine_near_miss_categories == (
        "edge",
        "spread",
        "price",
        "bucket_type",
        "liquidity",
        "depth",
    )
    assert calls["risk_rules"] == [{"action": "do_not_live_until_positive_markout"}]
    assert calls["risk_rules_build"]["include_quarantine_surface_rules"] is True
    assert calls["risk_rules_build"]["quarantine_journal_dir"] == _subjournal("/tmp/weather-paper", "quarantine")
    assert len(calls["journals"]) == 6
    assert calls["journals"][1]["journal_dir"] == _subjournal("/tmp/weather-paper", "quarantine")
    assert calls["journals"][1]["include_quarantine"] is True
    assert calls["journals"][2]["journal_dir"] == _subjournal("/tmp/weather-paper", "targeted_shadow")
    assert calls["journals"][2]["include_quarantine"] is True
    assert calls["journals"][3]["journal_dir"] == _subjournal("/tmp/weather-paper", "current_signal_taker")
    assert calls["journals"][3]["include_quarantine"] is True
    assert calls["journals"][4]["journal_dir"] == _subjournal("/tmp/weather-paper", "eq_shadow")
    assert calls["journals"][4]["include_quarantine"] is True
    assert calls["journals"][5]["journal_dir"] == _subjournal("/tmp/weather-paper", "maker_focus")
    assert calls["journals"][5]["include_quarantine"] is True
    assert len(calls["markouts"]) == 6
    assert {call["journal_dir"] for call in calls["markouts"]} == {
        "/tmp/weather-paper",
        _subjournal("/tmp/weather-paper", "quarantine"),
        _subjournal("/tmp/weather-paper", "targeted_shadow"),
        _subjournal("/tmp/weather-paper", "current_signal_taker"),
        _subjournal("/tmp/weather-paper", "eq_shadow"),
        _subjournal("/tmp/weather-paper", "maker_focus"),
    }
    assert len(calls["audits"]) == 6
    assert {call["journal_dir"] for call in calls["audits"]} == {
        "/tmp/weather-paper",
        _subjournal("/tmp/weather-paper", "quarantine"),
        _subjournal("/tmp/weather-paper", "targeted_shadow"),
        _subjournal("/tmp/weather-paper", "current_signal_taker"),
        _subjournal("/tmp/weather-paper", "eq_shadow"),
        _subjournal("/tmp/weather-paper", "maker_focus"),
    }
    assert len(calls["temperature_execution_writes"]) == 1
    assert calls["temperature_execution_writes"][0]["journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_execution"
    )
    assert len(calls["maker_markouts"]) == 1
    assert calls["maker_markouts"][0]["journal_dir"] == _subjournal("/tmp/weather-paper", "temperature_execution")
    assert calls["maker_markouts"][0]["min_markout_interval_seconds"] == 0.0
    assert len(calls["temperature_taker_runs"]) == 1
    assert calls["temperature_taker_runs"][0]["execution_journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_execution"
    )
    assert calls["temperature_taker_runs"][0]["taker_journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_taker"
    )
    assert calls["temperature_taker_runs"][0]["min_markout_interval_seconds"] == 600.0
    assert calls["readiness"]["temperature_taker_validation_report"]["hard_conclusion"] == (
        "temperature_taker_validation_collect_more_markouts"
    )
    assert calls["readiness"]["temperature_taker_journal_dir"] == _subjournal(
        "/tmp/weather-paper", "temperature_taker"
    )
    assert calls["readiness"]["include_temperature_taker_validation"] is True
    assert calls["readiness"]["current_signal_taker_validation_report"]["hard_conclusion"] == (
        "current_signal_taker_validation_collect_more_markouts"
    )


def test_weather_market_paper_cycle_expanded_weather_profile_scans_all_weather_families(monkeypatch, capsys):
    calls = {}

    def fake_polymarket(**kwargs):
        calls["polymarket"] = kwargs
        return {"rows": []}

    monkeypatch.setattr(cycle_cli, "build_polymarket_weather_payload", fake_polymarket)
    monkeypatch.setattr(cycle_cli, "build_scan_terminal_payload", lambda filters, force_refresh=False: {"rows": []})
    monkeypatch.setattr(cycle_cli, "temperature_model_targets_from_payload", lambda payload: {})
    monkeypatch.setattr(cycle_cli, "enrich_polymarket_payload_with_scan_models", lambda payload, scan_payload: payload)
    monkeypatch.setattr(
        cycle_cli,
        "build_weather_market_signal_report",
        lambda payload, config, risk_rules=None, risk_rule_mode="live", generated_at=None: {
            "summary": {"candidate_count": 0, "live_gate": False},
            "coverage_diagnostics": {"market_family_counts": []},
        },
    )
    monkeypatch.setattr(cycle_cli, "build_risk_rules_from_journal", lambda **kwargs: {"rule_count": 0, "rules": []})
    monkeypatch.setattr(cycle_cli, "write_paper_journal", lambda *args, **kwargs: {"fill_count": 0})
    monkeypatch.setattr(cycle_cli, "markout_open_paper_fills", lambda **kwargs: {"records": [], "marked_count": 0})
    monkeypatch.setattr(cycle_cli, "audit_paper_fills_resolution", lambda **kwargs: {"records": [], "resolved_count": 0})
    monkeypatch.setattr(cycle_cli, "build_resolved_gap_report", lambda **kwargs: {"rows": [], "hard_conclusion": "resolved_gap_wait_for_settlement"})
    monkeypatch.setattr(cycle_cli, "build_quality_threshold_calibration_report", lambda **kwargs: {"top_profiles": [], "hard_conclusion": "no_threshold_profile_passed_evidence_gate"})
    monkeypatch.setattr(cycle_cli, "build_quarantine_promotion_report", lambda **kwargs: {"groups": [], "hard_conclusion": "no_quarantine_group_ready_for_formal_paper"})
    monkeypatch.setattr(cycle_cli, "build_quarantine_surface_report", lambda **kwargs: {"groups": [], "hard_conclusion": "quarantine_surface_collect_more"})
    monkeypatch.setattr(cycle_cli, "build_targeted_shadow_validation_report", lambda **kwargs: {"hard_conclusion": "no_targeted_shadow_evidence"})
    monkeypatch.setattr(
        cycle_cli,
        "build_current_signal_taker_validation_report",
        lambda **kwargs: {"hard_conclusion": "current_signal_taker_validation_no_markouts"},
    )
    monkeypatch.setattr(cycle_cli, "summarize_execution_calibration", lambda *args, **kwargs: {"by_execution_and_spread": []})
    monkeypatch.setattr(
        cycle_cli,
        "build_maker_focus_signal_report",
        lambda *args, **kwargs: {
            "summary": {"maker_focus_count": 0, "live_gate": False},
            "selection_config": {},
            "eligible_group_count": 0,
            "eligible_groups": [],
            "hard_conclusion": "maker_focus_no_positive_historical_stratum",
        },
    )
    monkeypatch.setattr(
        cycle_cli,
        "run_temperature_taker_paper_cycle",
        lambda **kwargs: {
            "paper_only": True,
            "counts_for_live_gate": False,
            "taker_validation": {"hard_conclusion": "temperature_taker_validation_collect_more_markouts"},
        },
    )
    monkeypatch.setattr(cycle_cli, "build_live_readiness_report", lambda **kwargs: {"readiness_pct": 20.33, "live_gate": False})

    cycle_cli.main(["--production-profile", "--expanded-weather-profile", "--no-current-signal-report"])

    output = json.loads(capsys.readouterr().out)
    assert calls["polymarket"]["queries"] == ("temperature", "rain", "hurricane", "air quality")
    assert output["effective_profile"]["expanded_weather_profile"] is True
    assert output["effective_profile"]["polymarket_queries"] == ["temperature", "rain", "hurricane", "air quality"]
