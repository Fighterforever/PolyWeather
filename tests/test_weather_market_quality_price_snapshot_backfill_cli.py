from __future__ import annotations

import json

from scripts import weather_market_quality_price_snapshot_backfill as cli


def test_weather_market_quality_price_snapshot_backfill_cli_is_paper_only(monkeypatch, capsys, tmp_path):
    calls = {}

    def fake_replay(**kwargs):
        calls["replay"] = kwargs
        return {
            "schema_version": "polyweather_price_conditioned_snapshot_backfill.v1",
            "source_journal_dir": kwargs["source_journal_dir"],
            "target_journal_dir": kwargs["target_journal_dir"],
            "paper_only": True,
            "counts_for_live_gate": False,
            "snapshot_count": 2,
            "fill_count": 1,
            "duplicate_skipped_count": 1,
            "writes": [{"fill_count": 1}],
        }

    monkeypatch.setattr(cli, "replay_price_conditioned_snapshots_to_journal", fake_replay)
    monkeypatch.setattr(cli, "markout_open_paper_fills", lambda **kwargs: {"records": [{"fill_id": "x"}], "marked_count": 1})
    monkeypatch.setattr(cli, "audit_paper_fills_resolution", lambda **kwargs: {"records": [{"fill_id": "x"}], "resolved_count": 0})
    monkeypatch.setattr(cli, "write_maker_quote_journal_from_fills", lambda **kwargs: {"quote_records_written": 1})
    monkeypatch.setattr(cli, "markout_open_maker_quotes", lambda **kwargs: {"quote_markout_count": 1})
    monkeypatch.setattr(cli, "summarize_maker_quote_journal", lambda journal_dir: {"journal_dir": str(journal_dir), "quote_count": 1})
    monkeypatch.setattr(
        cli,
        "build_price_conditioned_validation_report",
        lambda **kwargs: {
            "report_type": "price_conditioned_validation",
            "paper_only": True,
            "counts_for_live_gate": False,
        },
    )

    cli.main(
        [
            "--source-journal-dir",
            str(tmp_path / "source"),
            "--target-journal-dir",
            str(tmp_path / "target"),
            "--sample-interval-minutes",
            "30",
            "--markout",
            "--audit",
            "--write-maker-quotes",
            "--validate",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["paper_only"] is True
    assert output["counts_for_live_gate"] is False
    assert output["backfill"]["fill_count"] == 1
    assert output["markout"] == {"marked_count": 1}
    assert output["resolved_audit"] == {"resolved_count": 0}
    assert output["maker_quote_summary"]["quote_count"] == 1
    assert output["validation"]["paper_only"] is True
    assert calls["replay"]["sample_interval_minutes"] == 30.0
