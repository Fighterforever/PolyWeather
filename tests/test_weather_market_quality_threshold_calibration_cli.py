from __future__ import annotations

import json

from scripts import weather_market_quality_threshold_calibration as calibration_cli


def test_quality_threshold_calibration_cli_passes_grid(monkeypatch, capsys):
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_quality_surface.v1",
            "report_type": "quality_threshold_calibration",
            "hard_conclusion": "no_threshold_profile_passed_evidence_gate",
        }

    monkeypatch.setattr(calibration_cli, "build_quality_threshold_calibration_report", fake_report)

    calibration_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/paper",
            "--quarantine-journal-dir",
            "/tmp/quarantine",
            "--bucket-type-exclusion-set",
            "eq",
            "--bucket-type-exclusion-set",
            "eq,range",
            "--min-price",
            "0.03",
            "--max-spread",
            "0.01",
            "--min-liquidity",
            "10",
            "--min-depth",
            "20",
            "--max-price",
            "0.8",
            "--min-marked-count",
            "7",
            "--min-mean-markout-cents",
            "0.2",
            "--min-win-rate",
            "0.6",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["report_type"] == "quality_threshold_calibration"
    assert calls["paper_journal_dir"] == "/tmp/paper"
    assert calls["quarantine_journal_dir"] == "/tmp/quarantine"
    assert calls["bucket_type_exclusion_sets"] == [("eq",), ("eq", "range")]
    assert calls["min_price_options"] == (0.03,)
    assert calls["max_spread_options"] == (0.01,)
    assert calls["min_liquidity_options"] == (10.0,)
    assert calls["min_depth_options"] == (20.0,)
    assert calls["max_price"] == 0.8
    assert calls["min_marked_count"] == 7
    assert calls["min_mean_markout_cents"] == 0.2
    assert calls["min_win_rate"] == 0.6
