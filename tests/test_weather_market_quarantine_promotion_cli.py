from __future__ import annotations

import json

from scripts import weather_market_quarantine_promotion as promotion_cli


def test_weather_market_quarantine_promotion_cli_reads_nested_cycle_signal_report(
    tmp_path,
    monkeypatch,
    capsys,
):
    cycle_report = tmp_path / "cycle.json"
    cycle_report.write_text(
        json.dumps({"signal_report": {"quarantine": [{"quarantine_reason": "risk_rule_only_reject"}]}}),
        encoding="utf-8",
    )
    calls = {}

    def fake_report(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_quality_surface.v1",
            "report_type": "quarantine_promotion",
            "hard_conclusion": "no_quarantine_group_ready_for_formal_paper",
        }

    monkeypatch.setattr(promotion_cli, "build_quarantine_promotion_report", fake_report)

    promotion_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/paper",
            "--quarantine-journal-dir",
            "/tmp/quarantine",
            "--signal-report",
            str(cycle_report),
            "--min-marked-count",
            "4",
            "--min-win-rate",
            "0.6",
            "--min-maker-markout-count",
            "2",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["paper_journal_dir"] == "/tmp/paper"
    assert calls["quarantine_journal_dir"] == "/tmp/quarantine"
    assert calls["signal_report"] == {"quarantine": [{"quarantine_reason": "risk_rule_only_reject"}]}
    assert calls["min_marked_count"] == 4
    assert calls["min_win_rate"] == 0.6
    assert calls["min_maker_markout_count"] == 2
    assert output["report_type"] == "quarantine_promotion"
