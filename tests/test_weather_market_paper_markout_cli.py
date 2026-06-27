from __future__ import annotations

import json

from scripts import weather_market_paper_markout as markout_cli


def test_weather_market_paper_markout_cli_prints_result(monkeypatch, capsys):
    calls = {}

    def fake_markout(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_paper_markout.v1",
            "open_fills_seen": 1,
            "markout_records_written": 1,
        }

    monkeypatch.setattr(markout_cli, "markout_open_paper_fills", fake_markout)

    markout_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/journal",
            "--max-fills",
            "3",
            "--min-markout-interval-seconds",
            "600",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["journal_dir"] == "/tmp/journal"
    assert calls["max_fills"] == 3
    assert calls["min_markout_interval_seconds"] == 600
    assert output["markout_records_written"] == 1
