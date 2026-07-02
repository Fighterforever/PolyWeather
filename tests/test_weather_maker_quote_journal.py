from __future__ import annotations

from src.trading.weather_maker_quote_journal import (
    build_maker_quote_markout_record,
    build_maker_quote_records,
    load_jsonl,
    markout_open_maker_quotes,
    summarize_maker_quote_journal,
    summarize_maker_quote_strata,
    write_maker_quote_journal_from_fills,
)
from src.trading.weather_paper_journal import _append_jsonl


def _fill():
    return {
        "fill_id": "fill-1",
        "run_id": "run-1",
        "recorded_at": "2026-06-26T19:00:00Z",
        "status": "open",
        "signal_bucket": "quarantine",
        "decision": "quarantine",
        "quarantine_reason": "single_non_risk_blocker:spread",
        "quarantine_blocker_scope": "single_non_risk_only",
        "quarantine_non_risk_blockers": ["spread_above_max"],
        "quarantine_non_risk_blocker_categories": ["spread"],
        "city": "seoul",
        "market_slug": "highest-temperature-in-seoul-on-june-28-2026-25c",
        "token_id": "yes-token",
        "side": "yes",
        "outcome": "Yes",
        "bucket_label": "= 25°C",
        "entry_price": 0.015,
        "entry_bid": 0.014,
        "entry_ask": 0.015,
        "entry_spread": 0.001,
        "edge_percent": 12.5,
        "model_probability": 0.14,
        "end_date": "2026-06-28T12:00:00Z",
    }


class CrossedBook:
    best_bid = 0.011
    best_ask = 0.014
    spread = 0.003
    bid_depth_usdc_3c = 12.0
    ask_depth_usdc_3c = 9.0


class UnfilledBook:
    best_bid = 0.017
    best_ask = 0.019
    spread = 0.002
    bid_depth_usdc_3c = 12.0
    ask_depth_usdc_3c = 9.0


class FakeClient:
    def __init__(self, book):
        self.book = book
        self.tokens = []

    def get_order_book(self, token_id: str):
        self.tokens.append(token_id)
        return self.book


def test_build_maker_quote_records_uses_entry_bid_and_excludes_live_gate():
    records = build_maker_quote_records([_fill()], quote_size=2.0, recorded_at="2026-06-26T19:01:00Z")

    assert len(records) == 1
    assert records[0]["quote_strategy"] == "maker_bid"
    assert records[0]["quote_reference_price"] == 0.014
    assert records[0]["quote_offset_cents"] == 0.0
    assert records[0]["quote_price"] == 0.014
    assert records[0]["quote_notional_usdc"] == 0.028
    assert records[0]["counts_for_live_gate"] is False
    assert records[0]["signal_bucket"] == "quarantine"
    assert records[0]["quarantine_reason"] == "single_non_risk_blocker:spread"
    assert records[0]["quarantine_non_risk_blocker_categories"] == ["spread"]
    assert records[0]["market_family"] == "temperature"
    assert records[0]["time_to_expiry_bucket"] == "1-2d"
    assert records[0]["fine_time_to_expiry_bucket"] == "36-48h"


def test_build_maker_quote_records_supports_explicit_price_ladder():
    records = build_maker_quote_records(
        [_fill()],
        quote_size=2.0,
        quote_offset_cents=[0, 1, 2, -1, 1],
        recorded_at="2026-06-26T19:01:00Z",
    )

    assert [(record["quote_strategy"], record["quote_price"]) for record in records] == [
        ("maker_bid", 0.014),
        ("maker_bid_minus_1c", 0.004),
    ]
    assert [record["quote_offset_cents"] for record in records] == [0.0, 1.0]
    assert records[1]["quote_notional_usdc"] == 0.008
    assert all(record["counts_for_live_gate"] is False for record in records)


def test_maker_quote_markout_inferrs_fill_only_when_ask_crosses_quote():
    quote = build_maker_quote_records([_fill()], recorded_at="2026-06-26T19:01:00Z")[0]

    crossed = build_maker_quote_markout_record(
        quote,
        book=CrossedBook(),
        recorded_at="2026-06-26T19:11:00Z",
    )
    unfilled = build_maker_quote_markout_record(
        quote,
        book=UnfilledBook(),
        recorded_at="2026-06-26T19:11:00Z",
    )

    assert crossed["status"] == "inferred_filled"
    assert crossed["fill_inference"] == "current_ask_at_or_below_quote"
    assert crossed["maker_markout_cents"] == -0.3
    assert crossed["market_family"] == "temperature"
    assert crossed["quote_reference_price"] == 0.014
    assert crossed["quote_offset_cents"] == 0.0
    assert crossed["maker_quote_price_bucket"] == "<0.03"
    assert crossed["entry_spread_bucket"] == "<=0.005"
    assert crossed["time_to_expiry_bucket"] == "1-2d"
    assert crossed["fine_time_to_expiry_bucket"] == "36-48h"
    assert crossed["bucket_type"] == "eq"
    assert crossed["quarantine_reason"] == "single_non_risk_blocker:spread"
    assert crossed["quarantine_non_risk_blocker_categories"] == ["spread"]
    assert crossed["missed_taker_markout_cents"] is None
    assert crossed["quote_horizon"] == "5-15m"
    assert unfilled["status"] == "resting_unfilled"
    assert unfilled["maker_markout_cents"] is None
    assert unfilled["missed_taker_markout_cents"] == 0.2


def test_write_and_markout_maker_quote_journal(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill()])

    quote_result = write_maker_quote_journal_from_fills(
        journal_dir=tmp_path,
        quote_size=1.0,
        recorded_at="2026-06-26T19:01:00Z",
    )
    duplicate_result = write_maker_quote_journal_from_fills(
        journal_dir=tmp_path,
        quote_size=1.0,
        recorded_at="2026-06-26T19:02:00Z",
    )
    markout_result = markout_open_maker_quotes(
        journal_dir=tmp_path,
        client=FakeClient(CrossedBook()),
        recorded_at="2026-06-26T19:11:00Z",
    )
    summary = summarize_maker_quote_journal(tmp_path)

    assert quote_result["quote_records_written"] == 1
    assert duplicate_result["quote_records_written"] == 0
    assert duplicate_result["duplicate_skipped_count"] == 1
    assert markout_result["open_quotes_seen"] == 1
    assert markout_result["inferred_fill_count"] == 1
    assert summary["quote_count"] == 1
    assert summary["inferred_fill_count"] == 1
    assert summary["mean_maker_markout_cents"] == -0.3
    assert len(load_jsonl(tmp_path / "maker_quotes.jsonl")) == 1


def test_markout_maker_quotes_skips_recent_marks_when_interval_set(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill()])
    write_maker_quote_journal_from_fills(
        journal_dir=tmp_path,
        quote_size=1.0,
        recorded_at="2026-06-26T19:01:00Z",
    )
    first = markout_open_maker_quotes(
        journal_dir=tmp_path,
        client=FakeClient(CrossedBook()),
        recorded_at="2026-06-26T19:11:00Z",
    )
    client = FakeClient(UnfilledBook())
    second = markout_open_maker_quotes(
        journal_dir=tmp_path,
        client=client,
        recorded_at="2026-06-26T19:15:00Z",
        min_markout_interval_seconds=600,
    )

    assert first["markout_records_written"] == 1
    assert second["open_quotes_seen"] == 0
    assert second["skipped_recent_count"] == 1
    assert second["min_markout_interval_seconds"] == 600.0
    assert client.tokens == []
    assert len(load_jsonl(tmp_path / "maker_quote_markouts.jsonl")) == 1


def test_summarize_maker_quote_journal_can_filter_near_miss_quarantine_reason(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill()])
    write_maker_quote_journal_from_fills(
        journal_dir=tmp_path,
        quote_size=1.0,
        recorded_at="2026-06-26T19:01:00Z",
    )
    markout_open_maker_quotes(
        journal_dir=tmp_path,
        client=FakeClient(CrossedBook()),
        recorded_at="2026-06-26T19:11:00Z",
    )

    risk_only = summarize_maker_quote_journal(
        tmp_path,
        quarantine_reasons=("risk_rule_only_reject",),
    )
    near_miss = summarize_maker_quote_strata(
        tmp_path,
        quarantine_reasons=("single_non_risk_blocker:spread",),
    )

    assert risk_only["quote_markout_count"] == 0
    assert near_miss["quote_markout_count"] == 1
    assert near_miss["by_quarantine_reason"][0]["quarantine_reason"] == "single_non_risk_blocker:spread"
    assert near_miss["by_quarantine_blocker_scope"][0]["quarantine_blocker_scope"] == "single_non_risk_only"


def test_write_maker_quote_journal_allows_distinct_ladder_quotes(tmp_path):
    _append_jsonl(tmp_path / "paper_fills.jsonl", [_fill()])

    quote_result = write_maker_quote_journal_from_fills(
        journal_dir=tmp_path,
        quote_size=1.0,
        quote_offset_cents=[0, 1],
        recorded_at="2026-06-26T19:01:00Z",
    )
    duplicate_result = write_maker_quote_journal_from_fills(
        journal_dir=tmp_path,
        quote_size=1.0,
        quote_offset_cents=[0, 1],
        recorded_at="2026-06-26T19:02:00Z",
    )
    quotes = load_jsonl(tmp_path / "maker_quotes.jsonl")

    assert quote_result["quote_records_written"] == 2
    assert quote_result["quote_offset_cents"] == [0.0, 1.0]
    assert duplicate_result["quote_records_written"] == 0
    assert duplicate_result["duplicate_skipped_count"] == 2
    assert [quote["quote_strategy"] for quote in quotes] == ["maker_bid", "maker_bid_minus_1c"]


def test_markout_maker_quotes_backfills_end_date_from_fill_context(tmp_path):
    fill = _fill()
    quote = build_maker_quote_records([fill], recorded_at="2026-06-26T19:01:00Z")[0]
    quote.pop("end_date", None)
    quote.pop("time_to_expiry_bucket", None)
    _append_jsonl(tmp_path / "paper_fills.jsonl", [fill])
    _append_jsonl(tmp_path / "maker_quotes.jsonl", [quote])

    markout_open_maker_quotes(
        journal_dir=tmp_path,
        client=FakeClient(CrossedBook()),
        recorded_at="2026-06-26T19:11:00Z",
    )

    markouts = load_jsonl(tmp_path / "maker_quote_markouts.jsonl")
    assert markouts[0]["time_to_expiry_bucket"] == "1-2d"


def test_summarize_maker_quote_strata_builds_negative_rules(tmp_path):
    quote = build_maker_quote_records(
        [_fill()],
        recorded_at="2026-06-26T19:01:00Z",
    )[0]
    records = [
        build_maker_quote_markout_record(
            {**quote, "quote_id": f"quote-{index}", "fill_id": f"fill-{index}"},
            book=CrossedBook(),
            recorded_at="2026-06-26T19:11:00Z",
        )
        for index in range(3)
    ]
    for record in records:
        record.pop("market_family", None)
    _append_jsonl(tmp_path / "maker_quote_markouts.jsonl", records)

    summary = summarize_maker_quote_strata(tmp_path, min_count=3)

    assert summary["quote_markout_count"] == 3
    assert summary["by_city"][0]["city"] == "seoul"
    assert summary["by_market_family"][0]["market_family"] == "temperature"
    assert summary["by_bucket_type"][0]["bucket_type"] == "eq"
    assert summary["by_quote_strategy"][0]["quote_strategy"] == "maker_bid"
    assert summary["by_time_to_expiry"][0]["time_to_expiry_bucket"] == "1-2d"
    assert summary["by_fine_time_to_expiry"][0]["fine_time_to_expiry_bucket"] == "36-48h"
    assert summary["by_fine_time_to_expiry_and_spread"][0]["fine_time_to_expiry_bucket"] == "36-48h"
    assert summary["by_strategy_and_spread"][0]["quote_strategy"] == "maker_bid"
    assert summary["by_strategy_and_fine_time_to_expiry_and_spread"][0]["quote_strategy"] == "maker_bid"
    assert any(
        rule["group"] == "maker_quote_by_city"
        and rule["dimensions"] == {"city": "seoul"}
        and rule["reason"] == "maker_adverse_selection"
        for rule in summary["do_not_live_rules"]
    )
    assert any(
        rule["dimensions"] == {"time_to_expiry_bucket": "1-2d"}
        for rule in summary["do_not_live_rules"]
    )
    assert any(
        rule["dimensions"] == {
            "fine_time_to_expiry_bucket": "36-48h",
            "entry_spread_bucket": "<=0.005",
        }
        for rule in summary["do_not_live_rules"]
    )
