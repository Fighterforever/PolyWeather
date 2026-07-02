from __future__ import annotations

import json

from scripts import weather_market_quarantine_surface as surface_cli


def test_weather_market_quarantine_surface_cli_passes_thresholds(monkeypatch, capsys):
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_quarantine_surface.v1",
            "hard_conclusion": "quarantine_surface_collect_more",
        }

    monkeypatch.setattr(surface_cli, "build_quarantine_surface_report", fake_report)

    surface_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/paper",
            "--quarantine-journal-dir",
            "/tmp/quarantine",
            "--min-decision-count",
            "7",
            "--min-promote-count",
            "9",
            "--min-mean-markout-cents",
            "0.2",
            "--min-win-rate",
            "0.6",
            "--min-maker-quote-count",
            "3",
            "--min-maker-mean-markout-cents",
            "0.1",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "polyweather_weather_quarantine_surface.v1"
    assert calls == {
        "paper_journal_dir": "/tmp/paper",
        "quarantine_journal_dir": "/tmp/quarantine",
        "min_decision_count": 7,
        "min_promote_count": 9,
        "min_mean_markout_cents": 0.2,
        "min_win_rate": 0.6,
        "min_maker_quote_count": 3,
        "min_maker_mean_markout_cents": 0.1,
    }
