from __future__ import annotations

import json

from scripts import weather_market_maker_quote_strata as strata_cli


def test_weather_market_maker_quote_strata_cli_prints_summary(monkeypatch, capsys):
    calls = {}

    def fake_summary(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {
            "schema_version": "polyweather_weather_maker_quote_markout.v1",
            "quote_markout_count": 3,
            "do_not_live_rules": [{"group": "maker_quote_by_city"}],
        }

    monkeypatch.setattr(strata_cli, "summarize_maker_quote_strata", fake_summary)

    strata_cli.main(["--paper-journal-dir", "/tmp/journal", "--all-observations", "--min-count", "3"])

    output = json.loads(capsys.readouterr().out)
    assert calls["args"] == ("/tmp/journal",)
    assert calls["kwargs"]["latest_only"] is False
    assert calls["kwargs"]["min_count"] == 3
    assert output["do_not_live_rules"][0]["group"] == "maker_quote_by_city"
