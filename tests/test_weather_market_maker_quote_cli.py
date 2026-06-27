from __future__ import annotations

import json

from scripts import weather_market_maker_quote as maker_cli


def test_weather_market_maker_quote_cli_writes_and_marks_quotes(monkeypatch, capsys):
    calls = {}

    def fake_write(**kwargs):
        calls["write"] = kwargs
        return {"quote_records_written": 2}

    def fake_markout(**kwargs):
        calls["markout"] = kwargs
        return {"inferred_fill_count": 1}

    def fake_summary(*args, **kwargs):
        calls["summary"] = {"args": args, "kwargs": kwargs}
        return {"quote_count": 2, "mean_maker_markout_cents": 0.1}

    monkeypatch.setattr(maker_cli, "write_maker_quote_journal_from_fills", fake_write)
    monkeypatch.setattr(maker_cli, "markout_open_maker_quotes", fake_markout)
    monkeypatch.setattr(maker_cli, "summarize_maker_quote_journal", fake_summary)

    maker_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/weather-quarantine",
            "--quote-size",
            "3",
            "--maker-quote-offset-cents",
            "0",
            "--maker-quote-offset-cents",
            "1",
            "--max-quotes",
            "5",
            "--markout-max-quotes",
            "7",
            "--min-markout-interval-seconds",
            "600",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["write"]["journal_dir"] == "/tmp/weather-quarantine"
    assert calls["write"]["quote_size"] == 3.0
    assert calls["write"]["quote_offset_cents"] == [0.0, 1.0]
    assert calls["write"]["max_quotes"] == 5
    assert calls["markout"]["max_quotes"] == 7
    assert calls["markout"]["min_markout_interval_seconds"] == 600.0
    assert output["quote_write"]["quote_records_written"] == 2
    assert output["summary"]["quote_count"] == 2


def test_weather_market_maker_quote_cli_can_markout_only(monkeypatch, capsys):
    monkeypatch.setattr(
        maker_cli,
        "write_maker_quote_journal_from_fills",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not write")),
    )
    monkeypatch.setattr(maker_cli, "markout_open_maker_quotes", lambda **kwargs: {"open_quotes_seen": 1})
    monkeypatch.setattr(maker_cli, "summarize_maker_quote_journal", lambda *args, **kwargs: {"quote_count": 1})

    maker_cli.main(["--paper-journal-dir", "/tmp/weather-quarantine", "--markout-only"])

    output = json.loads(capsys.readouterr().out)
    assert output["quote_write"] is None
    assert output["markout"]["open_quotes_seen"] == 1
