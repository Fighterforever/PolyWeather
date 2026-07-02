from __future__ import annotations

import json

from scripts import weather_market_quarantine_validation as validation_cli


def test_weather_market_quarantine_validation_cli_passes_thresholds(monkeypatch, capsys):
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_quarantine_validation.v1",
            "unlock_candidate_count": 0,
        }

    monkeypatch.setattr(validation_cli, "build_quarantine_validation_report", fake_report)

    validation_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/paper",
            "--quarantine-journal-dir",
            "/tmp/quarantine",
            "--min-unlock-count",
            "7",
            "--min-maker-inferred-fills",
            "4",
            "--min-unlock-mean-markout-cents",
            "0.1",
            "--min-unlock-win-rate",
            "0.6",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["unlock_candidate_count"] == 0
    assert calls == {
        "paper_journal_dir": "/tmp/paper",
        "quarantine_journal_dir": "/tmp/quarantine",
        "min_unlock_count": 7,
        "min_maker_inferred_fills": 4,
        "min_unlock_mean_markout_cents": 0.1,
        "min_unlock_win_rate": 0.6,
    }
