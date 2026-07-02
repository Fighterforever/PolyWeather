from __future__ import annotations

import json

from scripts import weather_market_resolved_audit as audit_cli


def test_weather_market_resolved_audit_cli_prints_result(monkeypatch, capsys):
    calls = {}

    def fake_audit(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_resolved_audit.v1",
            "fills_seen": 2,
            "resolved_count": 1,
        }

    monkeypatch.setattr(audit_cli, "audit_paper_fills_resolution", fake_audit)

    audit_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/journal",
            "--backfill-dir",
            "/tmp/backfill",
            "--max-fills",
            "2",
            "--resolved-only",
            "--no-backfill-fallback",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls["journal_dir"] == "/tmp/journal"
    assert calls["backfill_dir"] == "/tmp/backfill"
    assert calls["max_fills"] == 2
    assert calls["include_unresolved"] is False
    assert calls["use_backfill_fallback"] is False
    assert output["resolved_count"] == 1


def test_weather_market_resolved_audit_cli_can_compact_existing(monkeypatch, capsys):
    calls = {}

    def fake_compact(**kwargs):
        calls.update(kwargs)
        return {
            "schema_version": "polyweather_weather_resolved_audit.v1",
            "original_count": 8,
            "compacted_count": 3,
            "removed_count": 5,
        }

    monkeypatch.setattr(audit_cli, "compact_resolved_audit_log", fake_compact)

    audit_cli.main(
        [
            "--paper-journal-dir",
            "/tmp/journal",
            "--compact-existing",
            "--write",
            "--no-backup",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert calls == {"journal_dir": "/tmp/journal", "write": True, "backup": False}
    assert output["removed_count"] == 5
