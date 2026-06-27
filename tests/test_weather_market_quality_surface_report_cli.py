from __future__ import annotations

import json

from scripts import weather_market_quality_surface_report as quality_cli


def test_weather_market_quality_surface_report_cli_passes_filters(monkeypatch, capsys):
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_quality_surface.v1",
            "hard_conclusion": "quality_surface_needs_more_or_better_evidence",
        }

    monkeypatch.setattr(quality_cli, "build_quality_surface_report", fake_report)

    quality_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/paper",
            "--quarantine-journal-dir",
            "/tmp/quarantine",
            "--backfill-dir",
            "/tmp/backfill",
            "--allowed-side",
            "no",
            "--exclude-bucket-type",
            "eq",
            "--min-price",
            "0.04",
            "--max-spread",
            "0.01",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "polyweather_weather_quality_surface.v1"
    assert calls["paper_journal_dir"] == "/tmp/paper"
    assert calls["quarantine_journal_dir"] == "/tmp/quarantine"
    assert calls["backfill_dir"] == "/tmp/backfill"
    assert calls["allowed_sides"] == ["no"]
    assert calls["excluded_bucket_types"] == ["eq"]
    assert calls["min_price"] == 0.04
    assert calls["max_spread"] == 0.01
