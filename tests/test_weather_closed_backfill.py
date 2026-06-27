from __future__ import annotations

from src.trading.weather_closed_backfill import (
    build_closed_backfill_records,
    summarize_closed_backfill_journal,
    write_closed_backfill_journal,
)
from src.trading.weather_paper_journal import load_jsonl


def _closed_payload():
    return {
        "schema_version": "polyweather_polymarket_readonly_payload.v1",
        "snapshot_id": "closed-snapshot",
        "generated_at": "2026-06-26T20:00:00Z",
        "source": "polymarket_closed_readonly",
        "rows": [
            {
                "event_id": "event",
                "event_slug": "highest-temperature-in-nyc-on-june-25-2026",
                "event_title": "Highest temperature in NYC on June 25?",
                "market_id": "market",
                "market_slug": "highest-temperature-in-nyc-on-june-25-2026-between-86-87f",
                "question": "Will the highest temperature in New York City be between 86-87°F on June 25?",
                "closed": True,
                "outcome": "Yes",
                "side": "yes",
                "token_id": "yes-token",
                "market_probability": 1.0,
            },
            {
                "event_id": "event",
                "event_slug": "highest-temperature-in-nyc-on-june-25-2026",
                "event_title": "Highest temperature in NYC on June 25?",
                "market_id": "market",
                "market_slug": "highest-temperature-in-nyc-on-june-25-2026-between-86-87f",
                "question": "Will the highest temperature in New York City be between 86-87°F on June 25?",
                "closed": True,
                "outcome": "No",
                "side": "no",
                "token_id": "no-token",
                "market_probability": 0.0,
            },
        ],
    }


def test_build_closed_backfill_records_groups_market_resolution_and_parses_range():
    records = build_closed_backfill_records(
        _closed_payload(),
        recorded_at="2026-06-26T20:01:00Z",
    )

    assert len(records) == 1
    assert records[0]["backfill_only"] is True
    assert records[0]["counts_for_live_gate"] is False
    assert records[0]["status"] == "resolved"
    assert records[0]["winning_outcome"] == "Yes"
    assert records[0]["city"] == "new york"
    assert records[0]["bucket_label"] == "86-87°F"
    assert records[0]["parsed_temperature_spec"]["comparator"] == "range"


def test_write_closed_backfill_journal_persists_separate_backfill_files(tmp_path):
    summary = write_closed_backfill_journal(
        _closed_payload(),
        backfill_dir=tmp_path,
        recorded_at="2026-06-26T20:01:00Z",
    )

    assert summary["record_count"] == 1
    assert summary["backfill_resolved_count"] == 1
    assert summary["backfill_counts_for_live_gate"] is False
    rows = load_jsonl(tmp_path / "closed_markets.jsonl")
    assert len(rows) == 1
    assert rows[0]["evidence_class"] == "closed_market_backfill"
    journal_summary = summarize_closed_backfill_journal(tmp_path)
    assert journal_summary["backfill_record_count"] == 1
    assert journal_summary["backfill_unique_city_count"] == 1


def test_write_closed_backfill_journal_skips_duplicate_closed_markets(tmp_path):
    first = write_closed_backfill_journal(
        _closed_payload(),
        backfill_dir=tmp_path,
        recorded_at="2026-06-26T20:01:00Z",
    )
    second = write_closed_backfill_journal(
        _closed_payload(),
        backfill_dir=tmp_path,
        recorded_at="2026-06-26T20:02:00Z",
    )

    assert first["record_count"] == 1
    assert first["duplicate_skipped_count"] == 0
    assert second["record_count"] == 0
    assert second["duplicate_skipped_count"] == 1
    assert len(load_jsonl(tmp_path / "closed_markets.jsonl")) == 1
