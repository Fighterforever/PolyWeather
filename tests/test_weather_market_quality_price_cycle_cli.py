from __future__ import annotations

import json

from scripts import weather_market_quality_price_cycle as cycle_cli


def test_weather_market_quality_price_cycle_cli_is_paper_only(monkeypatch, capsys, tmp_path):
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({"source_snapshot_id": "signal", "assessments": []}), encoding="utf-8")
    calls = {}

    def fake_search(signal_report, **kwargs):
        calls["signal_report"] = signal_report
        calls["search_kwargs"] = kwargs
        return {
            "report_type": "price_conditioned_quality_search",
            "generated_at": kwargs["generated_at"],
            "source_signal_snapshot_id": "signal",
            "opportunity_count": 1,
            "strict_candidate_count": 0,
            "risk_only_quarantine_count": 1,
            "hard_conclusion": "price_conditioned_risk_only_quarantine_found",
            "opportunities": [{"row_id": "paris:no"}],
        }

    def fake_paper_report(price_search, **kwargs):
        calls["paper_report_input"] = price_search
        return {
            "summary": {
                "candidate_count": 0,
                "watch_count": 0,
                "quarantine_count": 1,
                "live_gate": False,
                "live_authorization_pct": 0,
            },
            "candidates": [],
            "watch": [],
            "quarantine": [{"row_id": "paris:no"}],
        }

    def fake_write(report, **kwargs):
        calls["write_report"] = report
        calls["write_kwargs"] = kwargs
        return {
            "journal_dir": str(kwargs["journal_dir"]),
            "fill_count": 1,
            "quarantine_count": 1,
            "paper_only": True,
        }

    monkeypatch.setattr(cycle_cli, "build_price_conditioned_quality_search", fake_search)
    monkeypatch.setattr(cycle_cli, "build_price_conditioned_paper_report", fake_paper_report)
    monkeypatch.setattr(cycle_cli, "write_paper_journal", fake_write)
    monkeypatch.setattr(
        cycle_cli,
        "markout_open_paper_fills",
        lambda **kwargs: {"records": [{"fill_id": "x"}], "marked_count": 1},
    )
    monkeypatch.setattr(
        cycle_cli,
        "audit_paper_fills_resolution",
        lambda **kwargs: {"records": [{"fill_id": "x"}], "resolved_count": 0},
    )
    monkeypatch.setattr(
        cycle_cli,
        "summarize_paper_journal",
        lambda journal_dir: {"journal_dir": str(journal_dir), "paper_fill_count": 1},
    )
    monkeypatch.setattr(
        cycle_cli,
        "write_maker_quote_journal_from_fills",
        lambda **kwargs: {"quote_records_written": 1, "counts_for_live_gate": False},
    )
    monkeypatch.setattr(
        cycle_cli,
        "markout_open_maker_quotes",
        lambda **kwargs: {"quote_markout_count": 1},
    )
    monkeypatch.setattr(
        cycle_cli,
        "summarize_maker_quote_journal",
        lambda journal_dir: {"journal_dir": str(journal_dir), "quote_count": 1},
    )
    monkeypatch.setattr(
        cycle_cli,
        "build_price_conditioned_validation_report",
        lambda **kwargs: {
            "report_type": "price_conditioned_validation",
            "paper_only": True,
            "counts_for_live_gate": False,
            "journal_dir": str(kwargs["journal_dir"]),
            "hard_conclusion": "collect_more_price_conditioned_evidence",
        },
    )

    cycle_cli.main(
        [
            "--signal-report",
            str(signal_path),
            "--backfill-dir",
            "/tmp/backfill",
            "--paper-journal-dir",
            str(tmp_path / "price-conditioned"),
            "--sample-interval-minutes",
            "15",
            "--allowed-side",
            "no",
            "--write-maker-quotes",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["paper_only"] is True
    assert output["live_order_path"] is False
    assert output["counts_for_live_gate"] is False
    assert output["price_search"]["opportunity_count"] == 1
    assert output["paper_report_summary"]["live_gate"] is False
    assert output["markout"] == {"marked_count": 1}
    assert output["resolved_audit"] == {"resolved_count": 0}
    assert output["validation"]["paper_only"] is True
    assert output["validation"]["counts_for_live_gate"] is False
    assert calls["signal_report"]["source_snapshot_id"] == "signal"
    assert calls["search_kwargs"]["backfill_dir"] == "/tmp/backfill"
    assert calls["search_kwargs"]["allowed_sides"] == ["no"]
    assert calls["write_kwargs"]["include_quarantine"] is True
    assert calls["write_kwargs"]["include_candidates"] is True
    assert calls["write_kwargs"]["include_watch"] is True
    assert calls["write_kwargs"]["min_reentry_seconds"] == 900
