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
