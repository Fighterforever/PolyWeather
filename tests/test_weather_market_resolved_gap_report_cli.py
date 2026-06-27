from __future__ import annotations

import json

from scripts import weather_market_resolved_gap_report as gap_cli


def test_weather_market_resolved_gap_report_cli_passes_paths(monkeypatch, capsys):
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_resolved_gap.v1",
            "hard_conclusion": "resolved_gap_wait_for_settlement",
        }

    monkeypatch.setattr(gap_cli, "build_resolved_gap_report", fake_report)

    gap_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/paper",
            "--backfill-dir",
            "/tmp/backfill",
            "--settlement-grace-hours",
            "12",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "polyweather_weather_resolved_gap.v1"
    assert calls == {
        "journal_dir": "/tmp/paper",
        "backfill_dir": "/tmp/backfill",
        "settlement_grace_hours": 12.0,
    }
