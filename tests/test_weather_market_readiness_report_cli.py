from __future__ import annotations

import json

from scripts import weather_market_readiness_report as readiness_cli


def test_weather_market_readiness_report_cli_prints_result(monkeypatch, tmp_path, capsys):
    signal_report = tmp_path / "signal.json"
    signal_report.write_text(json.dumps({"summary": {"candidate_count": 1}}), encoding="utf-8")
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
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["journal_dir"] == "/tmp/journal"
    assert calls["quarantine_journal_dir"] == "/tmp/quarantine"
    assert calls["signal_report"] == {"summary": {"candidate_count": 1}}
    assert calls["include_quarantine_surface"] is True
    assert calls["include_temperature_taker_validation"] is True
    assert calls["temperature_taker_journal_dir"] == "/tmp/taker"
    assert calls["live_permission"] is True
    assert calls["settlement_grace_hours"] == 12.0
    assert calls["quarantine_surface_min_decision_count"] == 2
    assert calls["quarantine_surface_min_promote_count"] == 3
    assert output["readiness_pct"] == 12.5
