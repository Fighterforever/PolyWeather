from __future__ import annotations

import json

from scripts import weather_market_temperature_execution_shadow as cli


def test_temperature_execution_shadow_cli_marks_and_summarizes(monkeypatch, capsys):
    calls = {}

    def fake_markout(**kwargs):
        calls["markout"] = kwargs
        return {
            "open_quotes_seen": 5,
            "skipped_recent_count": 0,
            "quote_markout_count": 5,
        }

    def fake_summary(journal_dir):
        calls["summary"] = journal_dir
        return {"quote_count": 5, "fill_inference_rate": 0.0}

    def fake_strata(*args, **kwargs):
        calls["strata"] = {"args": args, "kwargs": kwargs}
        return {
            "by_quote_strategy": [{"quote_strategy": "maker_bid_minus_3c", "count": 1}],
            "by_strategy_and_spread": [
                {
                    "quote_strategy": "maker_bid_minus_3c",
                    "entry_spread_bucket": "0.005-0.015",
                    "count": 1,
                }
            ],
            "by_strategy_and_fine_time_to_expiry_and_spread": [
                {
                    "quote_strategy": "maker_bid_minus_3c",
                    "fine_time_to_expiry_bucket": "6-12h",
                    "entry_spread_bucket": "0.005-0.015",
                    "count": 1,
                }
            ],
            "do_not_live_rules": [{"reason": "maker_no_fill_evidence"}],
        }

    def fake_validation(**kwargs):
        calls["validation"] = kwargs
        return {
            "hard_conclusion": "temperature_execution_shadow_taker_proxy_positive_maker_unfilled",
            "next_action": "collect_formal_taker_paper",
        }

    monkeypatch.setattr(cli, "markout_open_maker_quotes", fake_markout)
    monkeypatch.setattr(cli, "summarize_maker_quote_journal", fake_summary)
    monkeypatch.setattr(cli, "summarize_maker_quote_strata", fake_strata)
    monkeypatch.setattr(cli, "build_temperature_execution_shadow_validation_report", fake_validation)

    cli.main(
        [
            "--journal-dir",
            "/tmp/temp-exec",
            "--markout-max-quotes",
            "20",
            "--min-markout-interval-seconds",
            "900",
            "--all-observations",
            "--min-count",
            "2",
            "--validation-min-quote-markouts",
            "8",
            "--validation-min-missed-taker-markout-cents",
            "0.5",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["paper_only"] is True
    assert output["counts_for_live_gate"] is False
    assert output["markout"]["open_quotes_seen"] == 5
    assert output["summary"]["quote_count"] == 5
    assert output["validation"]["next_action"] == "collect_formal_taker_paper"
    assert output["strategy_strata"][0]["quote_strategy"] == "maker_bid_minus_3c"
    assert output["do_not_live_rules"][0]["reason"] == "maker_no_fill_evidence"
    assert calls["markout"]["journal_dir"] == "/tmp/temp-exec"
    assert calls["markout"]["max_quotes"] == 20
    assert calls["markout"]["min_markout_interval_seconds"] == 900.0
    assert calls["strata"]["args"] == ("/tmp/temp-exec",)
    assert calls["strata"]["kwargs"]["latest_only"] is False
    assert calls["strata"]["kwargs"]["min_count"] == 2
    assert calls["validation"]["journal_dir"] == "/tmp/temp-exec"
    assert calls["validation"]["min_quote_markouts"] == 8
    assert calls["validation"]["min_missed_taker_markout_cents"] == 0.5
