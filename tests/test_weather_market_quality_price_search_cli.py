from __future__ import annotations

import json

from scripts import weather_market_quality_price_search as price_cli


def test_weather_market_quality_price_search_cli_loads_signal_report(monkeypatch, capsys, tmp_path):
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({"source_snapshot_id": "signal", "assessments": []}), encoding="utf-8")
    calls = {}

    def fake_search(signal_report, **kwargs):
        calls["signal_report"] = signal_report
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_quality_surface.v1",
            "report_type": "price_conditioned_quality_search",
            "opportunity_count": 0,
        }

    monkeypatch.setattr(price_cli, "build_price_conditioned_quality_search", fake_search)

    price_cli.main(
        [
            "--signal-report",
            str(signal_path),
            "--backfill-dir",
            "/tmp/backfill",
            "--allowed-side",
            "no",
            "--base-rate-haircut",
            "0.25",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["opportunity_count"] == 0
    assert calls["signal_report"]["source_snapshot_id"] == "signal"
    assert calls["backfill_dir"] == "/tmp/backfill"
    assert calls["allowed_sides"] == ["no"]
    assert calls["base_rate_haircut"] == 0.25
