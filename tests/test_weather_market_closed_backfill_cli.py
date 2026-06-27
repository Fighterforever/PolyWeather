from __future__ import annotations

import json

from scripts import weather_market_closed_backfill as backfill_cli


def test_weather_market_closed_backfill_cli_prints_result(monkeypatch, capsys):
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_closed_backfill.v1",
            "backfill_summary": {"backfill_record_count": 3},
        }

    monkeypatch.setattr(backfill_cli, "run_closed_weather_backfill", fake_run)

    backfill_cli.main(
        [
            "--backfill-dir",
            "/tmp/backfill",
            "--polymarket-query",
            "highest temperature in NYC",
            "--max-records",
            "3",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["backfill_dir"] == "/tmp/backfill"
    assert calls["queries"] == ["highest temperature in NYC"]
    assert calls["max_records"] == 3
    assert output["backfill_summary"]["backfill_record_count"] == 3


def test_weather_market_closed_backfill_cli_supports_exact_market_slugs(monkeypatch, capsys):
    calls = {}

    def fake_targeted_run(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_closed_backfill.v1",
            "targeted": True,
            "backfill_summary": {"backfill_record_count": 1},
        }

    monkeypatch.setattr(
        backfill_cli,
        "run_targeted_closed_weather_backfill_from_market_slugs",
        fake_targeted_run,
    )

    backfill_cli.main(
        [
            "--backfill-dir",
            "/tmp/backfill",
            "--market-slug",
            "highest-temperature-in-seoul-on-june-28-2026-30corabove",
            "--polymarket-search-limit",
            "7",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["backfill_dir"] == "/tmp/backfill"
    assert calls["market_slugs"] == ["highest-temperature-in-seoul-on-june-28-2026-30corabove"]
    assert calls["search_limit_per_slug"] == 7
    assert output["targeted"] is True
