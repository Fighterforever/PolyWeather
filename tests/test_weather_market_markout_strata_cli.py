from __future__ import annotations

import json

from scripts import weather_market_markout_strata as strata_cli


def test_weather_market_markout_strata_cli_prints_summary(monkeypatch, capsys):
    calls = {}

    def fake_summary(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {
            "schema_version": "polyweather_weather_paper_markout.v1",
            "marked_count": 2,
            "by_horizon": [{"markout_horizon": "30-60m", "count": 2}],
        }

    monkeypatch.setattr(strata_cli, "summarize_markout_strata", fake_summary)

    strata_cli.main(["--paper-journal-dir", "/tmp/journal", "--all-observations", "--min-count", "2"])

    output = json.loads(capsys.readouterr().out)
    assert calls["args"] == ("/tmp/journal",)
    assert calls["kwargs"]["latest_only"] is False
    assert calls["kwargs"]["min_count"] == 2
    assert output["by_horizon"][0]["markout_horizon"] == "30-60m"
