from __future__ import annotations

from src.trading.weather_closed_backfill import (
    build_closed_backfill_records,
    build_targeted_closed_weather_payload_from_market_slugs,
    run_targeted_closed_weather_backfill_from_market_slugs,
    summarize_closed_backfill_journal,
    write_closed_backfill_journal,
)
from src.trading.weather_closed_historical_replay import build_preresolution_orderbook_replay_report
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


class FakeTargetedClient:
    def __init__(self):
        self.search_calls = []

    def public_search_events(self, query, *, limit=5):
        self.search_calls.append({"query": query, "limit": limit})
        if query == "missing-slug":
            return []
        market = {
            "id": "market",
            "slug": query,
            "question": "Will the highest temperature in Seoul be 30°C or above on June 28?",
            "closed": query != "open-slug",
        }
        return [
            {
                "id": "event",
                "slug": "highest-temperature-in-seoul-on-june-28-2026",
                "title": "Highest temperature in Seoul on June 28?",
                "closed": query != "open-slug",
                "markets": [market],
            }
        ]

    def market_to_signal_rows(self, event, market, *, include_order_books):
        assert include_order_books is False
        return [
            {
                "event_id": event["id"],
                "event_slug": event["slug"],
                "event_title": event["title"],
                "market_id": market["id"],
                "market_slug": market["slug"],
                "question": market["question"],
                "closed": True,
                "outcome": "Yes",
                "side": "yes",
                "token_id": "yes-token",
                "market_probability": 1.0,
                "end_date": "2026-06-28T12:00:00Z",
                "settlement_spec": {
                    "end_time": "2026-06-28T12:00:00Z",
                    "station_code": "RKSI",
                    "settlement_source": "metar",
                    "rule_hash": "rule-1",
                    "rule_text": "test rule",
                },
            },
            {
                "event_id": event["id"],
                "event_slug": event["slug"],
                "event_title": event["title"],
                "market_id": market["id"],
                "market_slug": market["slug"],
                "question": market["question"],
                "closed": True,
                "outcome": "No",
                "side": "no",
                "token_id": "no-token",
                "market_probability": 0.0,
                "end_date": "2026-06-28T12:00:00Z",
                "settlement_spec": {
                    "end_time": "2026-06-28T12:00:00Z",
                    "station_code": "RKSI",
                    "settlement_source": "metar",
                    "rule_hash": "rule-1",
                    "rule_text": "test rule",
                },
            },
        ]


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
    assert records[0]["winning_token_id"] == "yes-token"
    assert records[0]["token_id_by_outcome"] == {"Yes": "yes-token", "No": "no-token"}
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


def test_build_targeted_closed_weather_payload_from_exact_market_slugs():
    client = FakeTargetedClient()

    payload = build_targeted_closed_weather_payload_from_market_slugs(
        market_slugs=["highest-temperature-in-seoul-on-june-28-2026-30corabove", "missing-slug", "open-slug"],
        client=client,
        search_limit_per_slug=3,
    )

    assert payload["source"] == "polymarket_closed_targeted_readonly"
    assert payload["status"] == "ready"
    assert payload["diagnostics"]["requested_market_slug_count"] == 3
    assert payload["diagnostics"]["closed_market_slug_count"] == 1
    assert payload["diagnostics"]["missing_market_slugs"] == ["missing-slug"]
    assert payload["diagnostics"]["open_market_slugs"] == ["open-slug"]
    assert len(payload["rows"]) == 2
    assert client.search_calls[0] == {
        "query": "highest-temperature-in-seoul-on-june-28-2026-30corabove",
        "limit": 3,
    }


def test_run_targeted_closed_weather_backfill_writes_token_level_record(tmp_path):
    result = run_targeted_closed_weather_backfill_from_market_slugs(
        market_slugs=["highest-temperature-in-seoul-on-june-28-2026-30corabove"],
        backfill_dir=tmp_path,
        client=FakeTargetedClient(),
    )

    assert result["targeted"] is True
    assert result["payload_status"] == "ready"
    assert result["backfill_journal"]["record_count"] == 1
    rows = load_jsonl(tmp_path / "closed_markets.jsonl")
    assert rows[0]["status"] == "resolved"
    assert rows[0]["winning_token_id"] == "yes-token"
    assert rows[0]["token_id_by_outcome"] == {"Yes": "yes-token", "No": "no-token"}
    assert rows[0]["resolution_source"] == "metar"


def test_targeted_closed_backfill_can_feed_preresolution_orderbook_replay(tmp_path):
    run_targeted_closed_weather_backfill_from_market_slugs(
        market_slugs=["highest-temperature-in-seoul-on-june-28-2026-30corabove"],
        backfill_dir=tmp_path,
        client=FakeTargetedClient(),
    )
    closed_records = load_jsonl(tmp_path / "closed_markets.jsonl")

    report = build_preresolution_orderbook_replay_report(
        closed_records=closed_records,
        orderbook_snapshots=[
            {
                "recorded_at": "2026-06-28T10:00:00Z",
                "market_id": "market",
                "market_slug": "highest-temperature-in-seoul-on-june-28-2026-30corabove",
                "token_id": "yes-token",
                "side": "yes",
                "best_ask": 0.64,
                "best_bid": 0.60,
                "ask_ladder": [{"price": 0.64, "size": 2.0}],
                "bid_ladder": [{"price": 0.60, "size": 2.0}],
            }
        ],
        replay_time="2026-06-28T13:00:00Z",
    )

    assert report["hard_conclusion"] == "preresolution_orderbook_replay_ready_for_settlement_calibration"
    assert report["replay"]["fill_count"] == 1
    assert report["replay"]["resolved_pnl_usdc"] == 0.36
    assert report["historical_evidence"]["supplement_count"] == 1
