from __future__ import annotations

import json

from src.trading.weather_paper_journal import _append_jsonl, load_jsonl
from src.trading.weather_resolved_audit import (
    audit_paper_fills_resolution,
    build_resolved_gap_report,
    build_resolved_audit_record,
    compact_resolved_audit_log,
    market_from_closed_backfill_record,
    summarize_resolved_audits,
)


def _fill(**overrides):
    row = {
        "schema_version": "polyweather_weather_paper_fill.v1",
        "fill_id": "fill-yes",
        "run_id": "run",
        "recorded_at": "2026-06-26T19:00:00Z",
        "status": "open",
        "market_id": "m1",
        "market_slug": "highest-temperature-in-seoul-on-june-26-2026-23c",
        "token_id": "yes-token",
        "side": "yes",
        "outcome": "Yes",
        "entry_price": 0.029,
        "edge_percent": 8.0,
        "model_probability": 0.11,
        "end_date": "2026-06-26T12:00:00Z",
    }
    row.update(overrides)
    return row


def _closed_market(outcome_prices='["0", "1"]'):
    return {
        "id": "m1",
        "slug": "highest-temperature-in-seoul-on-june-26-2026-23c",
        "question": "Will the highest temperature in Seoul be 23°C on June 26?",
        "closed": True,
        "umaResolutionStatus": "resolved",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": outcome_prices,
        "clobTokenIds": '["yes-token", "no-token"]',
    }


def test_build_resolved_audit_record_yes_loses():
    record = build_resolved_audit_record(
        _fill(),
        _closed_market('["0", "1"]'),
        recorded_at="2026-06-27T00:00:00Z",
    )

    assert record["status"] == "resolved"
    assert record["winning"] is False
    assert record["payout"] == 0.0
    assert record["pnl_cents"] == -2.9
    assert record["roi_pct"] == -100.0


def test_build_resolved_audit_record_no_wins():
    record = build_resolved_audit_record(
        _fill(fill_id="fill-no", token_id="no-token", side="no", outcome="No", entry_price=0.031),
        _closed_market('["0", "1"]'),
        recorded_at="2026-06-27T00:00:00Z",
    )

    assert record["status"] == "resolved"
    assert record["winning"] is True
    assert record["payout"] == 1.0
    assert record["pnl_cents"] == 96.9


def test_build_resolved_audit_record_unresolved_market():
    market = _closed_market()
    market["closed"] = False

    record = build_resolved_audit_record(_fill(), market)

    assert record["status"] == "unresolved"
    assert record["payout"] is None
    assert record["pnl_cents"] is None


class FakeClient:
    def __init__(self, market):
        self.market = market
        self.ids = []

    def get_market_by_id(self, market_id: str):
        self.ids.append(market_id)
        return self.market

    def find_market_by_slug(self, market_slug: str):
        return self.market


def test_audit_paper_fills_resolution_appends_audit_rows(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill()])
    client = FakeClient(_closed_market('["0", "1"]'))

    result = audit_paper_fills_resolution(
        journal_dir=tmp_path,
        client=client,
        recorded_at="2026-06-27T00:00:00Z",
    )

    assert client.ids == ["m1"]
    assert result["audit_records_written"] == 1
    assert result["resolved_count"] == 1
    assert result["win_count"] == 0
    assert result["total_pnl_cents"] == -2.9
    rows = load_jsonl(tmp_path / "resolved_audits.jsonl")
    assert len(rows) == 1
    assert rows[0]["status"] == "resolved"
    summary = summarize_resolved_audits(tmp_path)
    assert summary["resolved_count"] == 1
    assert summary["mean_pnl_cents"] == -2.9
    assert summary["resolution_source_counts"][0] == {"resolution_source": "polymarket_api", "count": 1}


def test_audit_paper_fills_resolution_skips_unchanged_audit_state(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill()])
    client = FakeClient(_closed_market('["0", "1"]'))

    first = audit_paper_fills_resolution(
        journal_dir=tmp_path,
        client=client,
        recorded_at="2026-06-27T00:00:00Z",
    )
    second = audit_paper_fills_resolution(
        journal_dir=tmp_path,
        client=client,
        recorded_at="2026-06-27T00:05:00Z",
    )

    rows = load_jsonl(tmp_path / "resolved_audits.jsonl")
    assert first["audit_records_written"] == 1
    assert first["unchanged_skipped_count"] == 0
    assert second["audit_records_written"] == 0
    assert second["unchanged_skipped_count"] == 1
    assert second["resolved_count"] == 1
    assert len(rows) == 1


def test_compact_resolved_audit_log_preserves_state_transitions(tmp_path):
    _append_jsonl(
        tmp_path / "resolved_audits.jsonl",
        [
            {"fill_id": "fill-1", "status": "unresolved", "market_closed": False},
            {"fill_id": "fill-1", "status": "unresolved", "market_closed": False},
            {"fill_id": "fill-1", "status": "resolved", "market_closed": True, "payout": 1.0, "winning": True},
            {"fill_id": "fill-1", "status": "resolved", "market_closed": True, "payout": 1.0, "winning": True},
            {"fill_id": "fill-2", "status": "unresolved", "market_closed": False},
            {"fill_id": "fill-2", "status": "unresolved", "market_closed": False},
        ],
    )

    dry_run = compact_resolved_audit_log(journal_dir=tmp_path, write=False, compacted_at="2026-06-27T00:00:00Z")
    assert dry_run["original_count"] == 6
    assert dry_run["compacted_count"] == 3
    assert dry_run["removed_count"] == 3
    assert len(load_jsonl(tmp_path / "resolved_audits.jsonl")) == 6

    written = compact_resolved_audit_log(journal_dir=tmp_path, write=True, compacted_at="2026-06-27T00:00:00Z")
    rows = load_jsonl(tmp_path / "resolved_audits.jsonl")
    assert written["backup_path"] is not None
    assert written["removed_count"] == 3
    assert [row["status"] for row in rows] == ["unresolved", "resolved", "unresolved"]


def test_audit_paper_fills_resolution_can_fallback_to_closed_backfill(tmp_path):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(journal_dir / "paper_fills.jsonl", [_fill(market_id="m-backfill", side="no", outcome="No", entry_price=0.12)])
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "record_id": "closed-1",
                "recorded_at": "2026-06-27T01:00:00Z",
                "status": "resolved",
                "market_id": "m-backfill",
                "market_slug": "highest-temperature-in-seoul-on-june-26-2026-23c",
                "question": "Will the highest temperature in Seoul be 23°C on June 26?",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 0.0, "No": 1.0},
                "winning_side": "no",
                "winning_outcome": "No",
                "winning_token_id": "no-token",
                "winning_payout": 1.0,
            }
        ],
    )
    client = FakeClient({**_closed_market(), "closed": False})

    result = audit_paper_fills_resolution(
        journal_dir=journal_dir,
        backfill_dir=backfill_dir,
        client=client,
        recorded_at="2026-06-27T02:00:00Z",
    )

    assert result["resolved_count"] == 1
    assert result["api_resolved_count"] == 0
    assert result["backfill_match_count"] == 1
    assert result["backfill_resolved_count"] == 1
    rows = load_jsonl(journal_dir / "resolved_audits.jsonl")
    assert rows[0]["resolution_source"] == "closed_backfill"
    assert rows[0]["status"] == "resolved"
    assert rows[0]["winning"] is True
    assert rows[0]["pnl_cents"] == 88.0
    assert rows[0]["backfill_record_id"] == "closed-1"


def test_market_from_closed_backfill_record_preserves_outcome_order():
    market = market_from_closed_backfill_record(
        {
            "market_id": "m1",
            "market_slug": "slug",
            "question": "Question?",
            "outcomes": ["No", "Yes"],
            "settled_probability_by_outcome": {"No": 1.0, "Yes": 0.0},
            "winning_outcome": "No",
            "winning_token_id": "no-token",
        }
    )

    assert market["closed"] is True
    assert json.loads(market["outcomes"]) == ["No", "Yes"]
    assert json.loads(market["outcomePrices"]) == [1.0, 0.0]


def test_resolved_gap_report_classifies_due_and_backfill_gaps(tmp_path):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    fills = [
        _fill(fill_id="not-due", market_id="future", market_slug="future-slug", end_date="2026-06-28T12:00:00Z"),
        _fill(fill_id="grace", market_id="grace", market_slug="grace-slug", end_date="2026-06-27T00:00:00Z"),
        _fill(fill_id="overdue", market_id="overdue", market_slug="overdue-slug", end_date="2026-06-25T00:00:00Z"),
        _fill(fill_id="matched", market_id="matched", end_date="2026-06-25T00:00:00Z"),
    ]
    _append_jsonl(journal_dir / "paper_fills.jsonl", fills)
    _append_jsonl(
        journal_dir / "resolved_audits.jsonl",
        [
            {"fill_id": "not-due", "status": "unresolved", "market_closed": False},
            {"fill_id": "grace", "status": "unresolved", "market_closed": False},
            {"fill_id": "overdue", "status": "unresolved", "market_closed": False},
            {"fill_id": "matched", "status": "unresolved", "market_closed": False},
        ],
    )
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "record_id": "matched-backfill",
                "status": "resolved",
                "market_id": "matched",
                "market_slug": "highest-temperature-in-seoul-on-june-26-2026-23c",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 1.0, "No": 0.0},
                "winning_outcome": "Yes",
                "winning_token_id": "yes-token",
            }
        ],
    )

    report = build_resolved_gap_report(
        journal_dir=journal_dir,
        backfill_dir=backfill_dir,
        generated_at="2026-06-27T12:00:00Z",
        settlement_grace_hours=24,
    )

    statuses = {row["fill_id"]: row["gap_status"] for row in report["rows"]}
    assert statuses == {
        "not-due": "not_due",
        "grace": "within_settlement_grace",
        "overdue": "overdue_api_market_unclosed",
        "matched": "backfill_match_not_resolved",
    }
    assert report["overdue_count"] == 1
    assert report["hard_conclusion"] == "resolved_gap_contains_overdue_backfill_work"
    assert any(row["gap_status"] == "not_due" and row["count"] == 1 for row in report["gap_status_counts"])


def test_resolved_audit_json_is_serializable():
    record = build_resolved_audit_record(_fill(), _closed_market())
    assert json.loads(json.dumps(record))["schema_version"] == "polyweather_weather_resolved_audit.v1"
