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


def test_build_resolved_audit_record_preserves_entry_settlement_metadata():
    record = build_resolved_audit_record(
        _fill(
            city="seoul",
            bucket_label=">= 23°C",
            bucket_type="ge",
            strategy_id="tail_threshold",
            execution_style="taker_depth_checked",
            p_lcb=0.12,
            q_effective=0.029,
            cost=0.005,
            ev_safe=0.086,
            target_date="2026-06-26",
            end_time="2026-06-26T12:00:00Z",
            settlement_spec_status="supported",
            settlement_rule_hash="rule-seoul-ge-23",
            settlement_station_code="RKSI",
            settlement_station_label="Incheon International Airport",
            settlement_source="metar",
            settlement_timezone="UTC+09:00",
            settlement_metric="daily_high_temperature",
            settlement_unit="C",
            settlement_spec={
                "schema_version": "polyweather_weather_settlement_spec.v1",
                "status": "supported",
                "city": "seoul",
                "station_code": "RKSI",
                "settlement_source": "metar",
                "target_date": "2026-06-26",
                "end_time": "2026-06-26T12:00:00Z",
                "bucket_type": "ge",
                "threshold": 23.0,
                "rule_hash": "rule-seoul-ge-23",
            },
        ),
        market=_closed_market(),
        recorded_at="2026-06-27T00:00:00Z",
    )

    assert record["city"] == "seoul"
    assert record["bucket_type"] == "ge"
    assert record["strategy_id"] == "tail_threshold"
    assert record["p_lcb_at_entry"] == 0.12
    assert record["ev_safe_at_entry"] == 0.086
    assert record["target_date"] == "2026-06-26"
    assert record["end_time"] == "2026-06-26T12:00:00Z"
    assert record["settlement_station_code"] == "RKSI"
    assert record["settlement_source"] == "metar"
    assert record["settlement_rule_hash"] == "rule-seoul-ge-23"
    assert record["settlement_spec"]["rule_hash"] == "rule-seoul-ge-23"


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
    assert record["winning_token_id"] == "no-token"
    assert record["resolved_outcome"] == "No"
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
    _append_jsonl(
        journal_dir / "paper_fills.jsonl",
        [_fill(market_id="m-backfill", token_id="no-token", side="no", outcome="No", entry_price=0.12)],
    )
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
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_side": "no",
                "winning_outcome": "No",
                "winning_token_id": "no-token",
                "winning_payout": 1.0,
                "resolution_source": "polymarket_api",
                "rule_hash": "rule-hash",
                "official_final_value": 24.0,
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
    assert rows[0]["winning_token_id"] == "no-token"
    assert rows[0]["resolution_rule_hash"] == "rule-hash"
    assert rows[0]["official_final_value"] == 24.0
    assert rows[0]["pnl_cents"] == 88.0
    assert rows[0]["backfill_record_id"] == "closed-1"


def test_audit_backfill_reconstructs_losing_token_payout_by_token_id(tmp_path):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        journal_dir / "paper_fills.jsonl",
        [_fill(market_id="m-backfill", token_id="yes-token", side="yes", outcome="Yes", entry_price=0.40)],
    )
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "record_id": "closed-1",
                "status": "resolved",
                "market_id": "m-backfill",
                "market_slug": "highest-temperature-in-seoul-on-june-26-2026-23c",
                "outcomes": ["Yes", "No"],
                "settled_probability_by_outcome": {"Yes": 0.0, "No": 1.0},
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "winning_outcome": "No",
                "winning_token_id": "no-token",
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

    rows = load_jsonl(journal_dir / "resolved_audits.jsonl")
    assert result["resolved_count"] == 1
    assert rows[0]["status"] == "resolved"
    assert rows[0]["token_id"] == "yes-token"
    assert rows[0]["winning_token_id"] == "no-token"
    assert rows[0]["payout"] == 0.0
    assert rows[0]["winning"] is False
    assert rows[0]["pnl_cents"] == -40.0


def test_market_from_closed_backfill_record_preserves_outcome_order():
    market = market_from_closed_backfill_record(
        {
            "market_id": "m1",
            "market_slug": "slug",
            "question": "Question?",
            "outcomes": ["No", "Yes"],
            "settled_probability_by_outcome": {"No": 1.0, "Yes": 0.0},
            "token_id_by_outcome": {"No": "no-token", "Yes": "yes-token"},
            "winning_outcome": "No",
            "winning_token_id": "no-token",
        }
    )

    assert market["closed"] is True
    assert json.loads(market["outcomes"]) == ["No", "Yes"]
    assert json.loads(market["outcomePrices"]) == [1.0, 0.0]
    assert json.loads(market["clobTokenIds"]) == ["no-token", "yes-token"]


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


def test_resolved_gap_report_excludes_legacy_exact_bucket_from_live_gate(tmp_path):
    journal_dir = tmp_path / "journal"
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        journal_dir / "paper_fills.jsonl",
        [
            _fill(
                fill_id=f"eq-{index}",
                bucket_label="= 28°C",
                end_date="2026-06-28T12:00:00Z",
            )
            for index in range(3)
        ],
    )
    _append_jsonl(
        journal_dir / "resolved_audits.jsonl",
        [{"fill_id": f"eq-{index}", "status": "unresolved", "market_closed": False} for index in range(3)],
    )

    report = build_resolved_gap_report(
        journal_dir=journal_dir,
        backfill_dir=backfill_dir,
        generated_at="2026-06-27T12:00:00Z",
    )

    assert report["fill_count"] == 3
    assert report["live_gate_fill_count"] == 0
    assert report["live_gate_gap_status_counts"] == []
    assert {row["strategy_id"] for row in report["rows"]} == {"eq_exact_shadow"}
    assert all(row["counts_for_live_gate"] is False for row in report["rows"])


def test_resolved_audit_json_is_serializable():
    record = build_resolved_audit_record(_fill(), _closed_market())
    assert json.loads(json.dumps(record))["schema_version"] == "polyweather_weather_resolved_audit.v1"
