from __future__ import annotations

import json

from scripts import weather_market_execution_calibration as calibration_cli


def test_weather_market_execution_calibration_cli_prints_summary(monkeypatch, capsys):
    calls = {}

    def fake_summary(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {
            "schema_version": "polyweather_weather_execution_calibration.v1",
            "record_count": 3,
            "by_execution_mode": [{"execution_mode": "taker_ask", "mean_markout_cents": -0.5}],
        }

    monkeypatch.setattr(calibration_cli, "summarize_execution_calibration", fake_summary)

    calibration_cli.main(["--paper-journal-dir", "/tmp/journal", "--all-observations", "--min-count", "2"])

    output = json.loads(capsys.readouterr().out)
    assert calls["args"] == ("/tmp/journal",)
    assert calls["kwargs"]["latest_only"] is False
    assert calls["kwargs"]["min_count"] == 2
    assert output["by_execution_mode"][0]["execution_mode"] == "taker_ask"
