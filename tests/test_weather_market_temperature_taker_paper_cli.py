from __future__ import annotations

import json

from scripts import weather_market_temperature_taker_paper as cli


def test_temperature_taker_paper_cli_runs_cycle(monkeypatch, capsys):
    calls = {}

    def fake_cycle(**kwargs):
        calls["cycle"] = kwargs
        return {
            "schema_version": "polyweather_weather_temperature_taker_paper.v1",
            "paper_only": True,
            "counts_for_live_gate": False,
            "hard_conclusion": "temperature_taker_paper_collect_formal",
            "journal": {"fill_count": 1},
            "markout": {"marked_count": 1, "mean_markout_cents": 1.0},
        }

    monkeypatch.setattr(cli, "run_temperature_taker_paper_cycle", fake_cycle)

    cli.main(
        [
            "--execution-journal-dir",
            "/tmp/execution",
            "--taker-journal-dir",
            "/tmp/taker",
            "--max-items",
            "3",
            "--max-fills",
            "2",
            "--markout-max-fills",
            "4",
            "--min-reentry-seconds",
            "1800",
            "--min-markout-interval-seconds",
            "900",
            "--validation-min-marked-count",
            "12",
            "--validation-min-mean-markout-cents",
            "0.5",
            "--validation-min-win-rate",
            "0.6",
            "--validation-required-horizon",
            "0-5m",
            "--validation-required-horizon",
            "5-15m",
            "--validation-min-horizon-count",
            "4",
            "--validation-min-resolved-count",
            "2",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["hard_conclusion"] == "temperature_taker_paper_collect_formal"
    assert output["journal"]["fill_count"] == 1
    assert calls["cycle"]["execution_journal_dir"] == "/tmp/execution"
    assert calls["cycle"]["taker_journal_dir"] == "/tmp/taker"
    assert calls["cycle"]["max_items"] == 3
    assert calls["cycle"]["max_fills"] == 2
    assert calls["cycle"]["markout_max_fills"] == 4
    assert calls["cycle"]["min_reentry_seconds"] == 1800
    assert calls["cycle"]["min_markout_interval_seconds"] == 900.0
    assert calls["cycle"]["validation_min_marked_count"] == 12
    assert calls["cycle"]["validation_min_mean_markout_cents"] == 0.5
    assert calls["cycle"]["validation_min_win_rate"] == 0.6
    assert calls["cycle"]["validation_required_horizons"] == ["0-5m", "5-15m"]
    assert calls["cycle"]["validation_min_horizon_count"] == 4
    assert calls["cycle"]["validation_min_resolved_count"] == 2
