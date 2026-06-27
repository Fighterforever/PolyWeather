from __future__ import annotations

import json

from scripts import weather_market_targeted_shadow_validation as validation_cli


def test_weather_market_targeted_shadow_validation_cli_prints_report(monkeypatch, capsys):
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_targeted_shadow.v1",
            "report_type": "targeted_shadow_validation",
            "hard_conclusion": "targeted_shadow_collect_more_or_reject",
        }

    monkeypatch.setattr(validation_cli, "build_targeted_shadow_validation_report", fake_report)

    validation_cli.main(
        [
            "--journal-dir",
            "/tmp/targeted",
            "--min-marked-count",
            "4",
            "--min-win-rate",
            "0.6",
            "--min-maker-inferred-fills",
            "2",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["journal_dir"] == "/tmp/targeted"
    assert calls["min_marked_count"] == 4
    assert calls["min_win_rate"] == 0.6
    assert calls["min_maker_inferred_fills"] == 2
    assert output["report_type"] == "targeted_shadow_validation"
