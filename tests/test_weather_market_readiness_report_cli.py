from __future__ import annotations

import json

from scripts import weather_market_readiness_report as readiness_cli
from src.trading.weather_current_signal import write_current_signal_report


def test_weather_market_readiness_report_cli_prints_result(monkeypatch, tmp_path, capsys):
    signal_report = tmp_path / "signal.json"
    signal_report.write_text(json.dumps({"summary": {"candidate_count": 1}}), encoding="utf-8")
    replay_report = tmp_path / "strict_replay.json"
    replay_report.write_text(
        json.dumps({"schema_version": "polyweather_weather_strict_gate_replay.v1"}),
        encoding="utf-8",
    )
    calibration_report = tmp_path / "settlement_calibration.json"
    calibration_report.write_text(
        json.dumps({"schema_version": "polyweather_weather_settlement_calibration.v1"}),
        encoding="utf-8",
    )
    paper_cycle_report = tmp_path / "paper_cycle.json"
    paper_cycle_report.write_text(
        json.dumps(
            {
                "schema_version": "polyweather_weather_paper_cycle.v1",
                "orderbook_archive_coverage": {
                    "schema_version": "polyweather_weather_orderbook_archive_coverage.v1",
                    "hard_conclusion": "orderbook_archive_coverage_ready",
                },
            }
        ),
        encoding="utf-8",
    )
    calls = {}

    def fake_build(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_live_readiness.v1",
            "readiness_pct": 12.5,
            "live_gate": False,
        }

    monkeypatch.setattr(readiness_cli, "build_live_readiness_report", fake_build)

    readiness_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/journal",
            "--signal-report",
            str(signal_report),
            "--live-permission",
            "--include-quarantine-surface",
            "--include-temperature-taker-validation",
            "--temperature-taker-journal-dir",
            "/tmp/taker",
            "--quarantine-journal-dir",
            "/tmp/quarantine",
            "--quarantine-surface-min-decision-count",
            "2",
            "--quarantine-surface-min-promote-count",
            "3",
            "--settlement-grace-hours",
            "12",
            "--strict-gate-replay-report",
            str(replay_report),
            "--settlement-calibration-report",
            str(calibration_report),
            "--orderbook-archive-coverage-report",
            str(paper_cycle_report),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["journal_dir"] == "/tmp/journal"
    assert calls["quarantine_journal_dir"] == "/tmp/quarantine"
    assert calls["signal_report"] == {"summary": {"candidate_count": 1}}
    assert calls["strict_gate_replay_report"] == {
        "schema_version": "polyweather_weather_strict_gate_replay.v1"
    }
    assert calls["settlement_calibration_report"] == {
        "schema_version": "polyweather_weather_settlement_calibration.v1"
    }
    assert calls["orderbook_archive_coverage_report"] == {
        "schema_version": "polyweather_weather_orderbook_archive_coverage.v1",
        "hard_conclusion": "orderbook_archive_coverage_ready",
    }
    assert calls["include_quarantine_surface"] is True
    assert calls["include_temperature_taker_validation"] is True
    assert calls["temperature_taker_journal_dir"] == "/tmp/taker"
    assert calls["live_permission"] is True
    assert calls["settlement_grace_hours"] == 12.0
    assert calls["quarantine_surface_min_decision_count"] == 2
    assert calls["quarantine_surface_min_promote_count"] == 3
    assert output["readiness_pct"] == 12.5


def test_weather_market_readiness_report_cli_auto_loads_latest_current_signal(monkeypatch, tmp_path, capsys):
    journal_dir = tmp_path / "journal"
    signal_dir = journal_dir / "current_signal_reports"
    write_current_signal_report(
        {"summary": {"candidate_count": 3, "watch_count": 1}},
        report_dir=signal_dir,
        generated_at="2026-06-27T00:00:00Z",
        source="test",
    )
    calls = {}

    def fake_build(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_live_readiness.v1",
            "readiness_pct": 20.0,
            "live_gate": False,
        }

    monkeypatch.setattr(readiness_cli, "build_live_readiness_report", fake_build)

    readiness_cli.main(["--paper-journal-dir", str(journal_dir)])

    output = json.loads(capsys.readouterr().out)
    assert calls["signal_report"] == {"summary": {"candidate_count": 3, "watch_count": 1}}
    assert output["readiness_pct"] == 20.0


def test_weather_market_readiness_report_cli_loads_live_evidence_bundle(monkeypatch, tmp_path, capsys):
    bundle_report = tmp_path / "bundle.json"
    bundle_report.write_text(
        json.dumps(
            {
                "schema_version": "polyweather_weather_live_evidence_bundle.v1",
                "strict_gate_replay_report": {
                    "schema_version": "polyweather_weather_strict_gate_replay.v1",
                    "hard_conclusion": "strict_gate_replay_ready_for_ev_audit",
                },
                "settlement_calibration_report": {
                    "schema_version": "polyweather_weather_settlement_calibration.v1",
                    "hard_conclusion": "settlement_calibration_ready_diagnostic_only",
                },
            }
        ),
        encoding="utf-8",
    )
    calls = {}

    def fake_build(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_live_readiness.v1",
            "readiness_pct": 33.0,
            "live_gate": False,
        }

    monkeypatch.setattr(readiness_cli, "build_live_readiness_report", fake_build)

    readiness_cli.main(
        [
            "--paper-journal-dir",
            str(tmp_path / "journal"),
            "--live-evidence-bundle-report",
            str(bundle_report),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["strict_gate_replay_report"]["schema_version"] == "polyweather_weather_strict_gate_replay.v1"
    assert calls["settlement_calibration_report"]["schema_version"] == "polyweather_weather_settlement_calibration.v1"
    assert output["readiness_pct"] == 33.0
