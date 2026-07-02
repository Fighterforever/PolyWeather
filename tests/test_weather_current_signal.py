from __future__ import annotations

from src.trading.weather_current_signal import (
    load_latest_current_signal_report,
    write_current_signal_report,
)
from src.trading.weather_paper_journal import load_jsonl


def test_current_signal_report_snapshot_round_trips_latest_and_manifest(tmp_path):
    report = {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "source_snapshot_id": "scan-1",
        "summary": {
            "candidate_count": 2,
            "watch_count": 1,
            "quarantine_count": 3,
            "reject_count": 4,
        },
    }

    manifest = write_current_signal_report(
        report,
        report_dir=tmp_path,
        generated_at="2026-06-27T00:00:00Z",
        source="test",
    )

    assert manifest["schema_version"] == "polyweather_weather_current_signal_snapshot.v1"
    assert manifest["paper_only"] is True
    assert manifest["counts_for_live_gate"] is False
    assert manifest["candidate_count"] == 2
    assert manifest["watch_count"] == 1
    assert load_latest_current_signal_report(report_dir=tmp_path) == report
    manifest_rows = load_jsonl(tmp_path / "manifest.jsonl")
    assert manifest_rows[-1]["snapshot_id"] == manifest["snapshot_id"]
