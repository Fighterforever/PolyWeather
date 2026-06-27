from __future__ import annotations

import json

from src.trading.weather_paper_journal import (
    build_paper_fill_records,
    load_jsonl,
    markout_open_paper_fills,
    summarize_markout_strata,
    summarize_paper_journal,
    write_paper_journal,
)


def _sample_report():
    return {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "generated_at": "2026-06-26T19:20:00Z",
        "source_snapshot_id": "poly-snapshot",
        "source_status": "ready",
        "source": "polymarket_readonly",
        "summary": {
            "candidate_count": 1,
            "watch_count": 1,
            "reject_count": 0,
            "live_gate": False,
            "live_authorization_pct": 0,
        },
        "candidates": [
            {
                "decision": "candidate",
                "row_id": "market:yes",
                "city": "seoul",
                "event_title": "Highest temperature in Seoul on June 27?",
                "question": "Will the highest temperature in Seoul be 31C or higher on June 27?",
                "market_id": "market",
                "market_slug": "highest-temperature-in-seoul-on-june-27-2026-31corhigher",
                "token_id": "yes-token",
                "side": "yes",
                "outcome": "Yes",
                "bucket_label": ">= 31°C",
                "bucket_type": "ge",
                "strategy_id": "tail_threshold",
                "execution_style": "taker_depth_checked",
                "why_now": "threshold bucket can be compared against calibrated station CDF",
                "strategy_live_eligible": True,
                "risk_caps": {"max_position_usdc": 7.0},
                "price": 0.029,
                "bid": 0.023,
                "ask": 0.029,
                "spread": 0.006,
                "liquidity": 0.65,
                "bid_depth_usdc_3c": 12.0,
                "ask_depth_usdc_3c": 8.0,
                "edge_percent": 7.8892,
                "p_lcb": 0.078,
                "q_effective": 0.029,
                "cost": 0.005,
                "ev_safe": 0.044,
                "model_probability": 0.107892,
                "market_probability": 0.02,
                "market_implied_cdf": 0.98,
                "market_implied_cdf_raw": 0.98,
                "market_implied_bucket_family": "threshold_cdf",
                "score": 97.85,
                "target_date": "2026-06-27",
                "end_time": "2026-06-27T12:00:00Z",
                "end_date": "2026-06-27T12:00:00Z",
                "settlement_spec_status": "supported",
                "settlement_rule_hash": "rule-seoul-ge-31",
                "settlement_station_code": "RKSI",
                "settlement_station_label": "Incheon International Airport",
                "settlement_source": "metar",
                "settlement_timezone": "UTC+09:00",
                "settlement_metric": "daily_high_temperature",
                "settlement_unit": "C",
                "settlement_spec": {
                    "schema_version": "polyweather_weather_settlement_spec.v1",
                    "status": "supported",
                    "city": "seoul",
                    "station_code": "RKSI",
                    "settlement_source": "metar",
                    "target_date": "2026-06-27",
                    "end_time": "2026-06-27T12:00:00Z",
                    "bucket_type": "ge",
                    "threshold": 31.0,
                    "rule_hash": "rule-seoul-ge-31",
                },
                "blockers": [],
                "warnings": [],
            }
        ],
        "watch": [
            {
                "decision": "watch",
                "row_id": "watch:yes",
                "market_slug": "watch-market",
                "token_id": "watch-token",
                "side": "yes",
                "price": 0.003,
                "edge_percent": 10.6,
                "model_probability": 0.109,
                "warnings": ["missing_spread"],
            }
        ],
        "quarantine": [
            {
                "decision": "quarantine",
                "original_decision": "reject",
                "quarantine_reason": "single_non_risk_blocker:spread",
                "quarantine_blocker_scope": "single_non_risk_plus_risk",
                "quarantine_non_risk_blockers": ["spread_above_max"],
                "quarantine_non_risk_blocker_categories": ["spread"],
                "would_be_decision_without_risk_rules": "candidate",
                "would_be_decision_without_quarantine_blockers": "candidate",
                "row_id": "quarantine:yes",
                "market_slug": "quarantine-market",
                "token_id": "quarantine-token",
                "side": "yes",
                "price": 0.031,
                "bid": 0.027,
                "ask": 0.031,
                "spread": 0.004,
                "edge_percent": 11.6,
                "model_probability": 0.147,
                "risk_rule_hits": ["negative_markout_rule:by_city:city=seoul"],
                "blockers": ["negative_markout_rule:by_city:city=seoul"],
                "warnings": [],
                "live_gate_excluded": True,
                "counts_for_live_gate": False,
            }
        ],
    }


def test_build_paper_fill_records_defaults_to_candidates_only():
    records = build_paper_fill_records(_sample_report(), recorded_at="2026-06-26T19:21:00Z")

    assert len(records) == 1
    assert records[0]["paper_only"] is True
    assert records[0]["status"] == "open"
    assert records[0]["signal_bucket"] == "candidates"
    assert records[0]["market_family"] == "temperature"
    assert records[0]["token_id"] == "yes-token"
    assert records[0]["entry_price"] == 0.029
    assert records[0]["entry_bid_depth_usdc_3c"] == 12.0
    assert records[0]["entry_ask_depth_usdc_3c"] == 8.0
    assert records[0]["strategy_id"] == "tail_threshold"
    assert records[0]["strategy_live_eligible"] is True
    assert records[0]["risk_caps"] == {"max_position_usdc": 7.0}
    assert records[0]["ev_safe"] == 0.044
    assert records[0]["q_effective"] == 0.029
    assert records[0]["market_implied_cdf"] == 0.98
    assert records[0]["target_date"] == "2026-06-27"
    assert records[0]["end_time"] == "2026-06-27T12:00:00Z"
    assert records[0]["settlement_station_code"] == "RKSI"
    assert records[0]["settlement_source"] == "metar"
    assert records[0]["settlement_rule_hash"] == "rule-seoul-ge-31"
    assert records[0]["settlement_spec"]["end_time"] == "2026-06-27T12:00:00Z"
    assert records[0]["live_gate_at_record"] is False
    assert records[0]["live_authorization_pct_at_record"] == 0


def test_build_paper_fill_records_can_write_only_quarantine_rows():
    records = build_paper_fill_records(
        _sample_report(),
        include_candidates=False,
        include_quarantine=True,
        recorded_at="2026-06-26T19:21:00Z",
    )

    assert len(records) == 1
    assert records[0]["signal_bucket"] == "quarantine"
    assert records[0]["decision"] == "quarantine"
    assert records[0]["original_decision"] == "reject"
    assert records[0]["quarantine_reason"] == "single_non_risk_blocker:spread"
    assert records[0]["quarantine_non_risk_blocker_categories"] == ["spread"]
    assert records[0]["would_be_decision_without_risk_rules"] == "candidate"
    assert records[0]["would_be_decision_without_quarantine_blockers"] == "candidate"
    assert records[0]["risk_rule_hits"] == ["negative_markout_rule:by_city:city=seoul"]
    assert records[0]["live_gate_excluded"] is True
    assert records[0]["counts_for_live_gate"] is False


def test_write_paper_journal_persists_snapshot_manifest_and_fills(tmp_path):
    summary = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        include_watch=True,
        recorded_at="2026-06-26T19:21:00Z",
    )

    assert summary["paper_only"] is True
    assert summary["fill_count"] == 2
    snapshot = json.loads((tmp_path / "snapshots" / f"{summary['run_id']}.json").read_text())
    assert snapshot["report"]["source_snapshot_id"] == "poly-snapshot"
    fills = load_jsonl(tmp_path / "paper_fills.jsonl")
    manifest = load_jsonl(tmp_path / "manifest.jsonl")
    assert len(fills) == 2
    assert len(manifest) == 1
    assert {row["signal_bucket"] for row in fills} == {"candidates", "watch"}
    journal_summary = summarize_paper_journal(tmp_path)
    assert journal_summary["paper_fill_count"] == 2
    assert journal_summary["candidate_fill_count"] == 1
    assert journal_summary["watch_fill_count"] == 1
    assert journal_summary["quarantine_fill_count"] == 0


def test_write_paper_journal_can_persist_quarantine_in_separate_channel(tmp_path):
    summary = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="quarantine-paper",
        include_candidates=False,
        include_quarantine=True,
        recorded_at="2026-06-26T19:21:00Z",
    )

    fills = load_jsonl(tmp_path / "paper_fills.jsonl")
    manifest = load_jsonl(tmp_path / "manifest.jsonl")
    assert summary["fill_count"] == 1
    assert summary["include_candidates"] is False
    assert summary["include_quarantine"] is True
    assert fills[0]["signal_bucket"] == "quarantine"
    assert fills[0]["counts_for_live_gate"] is False
    assert manifest[0]["include_quarantine"] is True
    assert summarize_paper_journal(tmp_path)["quarantine_fill_count"] == 1


def test_write_paper_journal_skips_duplicate_open_fills(tmp_path):
    first = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:21:00Z",
    )
    second = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:22:00Z",
    )

    assert first["fill_count"] == 1
    assert first["duplicate_skipped_count"] == 0
    assert second["fill_count"] == 0
    assert second["duplicate_skipped_count"] == 1
    assert summarize_paper_journal(tmp_path)["paper_fill_count"] == 1


def test_write_paper_journal_allows_reentry_after_min_interval(tmp_path):
    first = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="price-conditioned",
        recorded_at="2026-06-26T19:21:00Z",
        min_reentry_seconds=3600,
    )
    too_soon = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="price-conditioned",
        recorded_at="2026-06-26T19:50:00Z",
        min_reentry_seconds=3600,
    )
    later = write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="price-conditioned",
        recorded_at="2026-06-26T20:22:00Z",
        min_reentry_seconds=3600,
    )

    fills = load_jsonl(tmp_path / "paper_fills.jsonl")
    assert first["fill_count"] == 1
    assert too_soon["fill_count"] == 0
    assert too_soon["duplicate_skipped_count"] == 1
    assert later["fill_count"] == 1
    assert len(fills) == 2
    assert fills[0]["fill_id"] != fills[1]["fill_id"]


class FakeBook:
    token_id = "yes-token"
    market = "0xmarket"
    timestamp = "123"
    best_bid = 0.041
    best_ask = 0.045
    spread = 0.004
    bid_depth_usdc_3c = 12.0
    ask_depth_usdc_3c = 8.0
    bid_levels = 3
    ask_levels = 2


class FakeClient:
    def __init__(self):
        self.tokens = []

    def get_order_book(self, token_id: str):
        self.tokens.append(token_id)
        return FakeBook()


def test_markout_open_paper_fills_appends_conservative_exit_marks(tmp_path):
    write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:21:00Z",
    )
    client = FakeClient()

    result = markout_open_paper_fills(
        journal_dir=tmp_path,
        client=client,
        recorded_at="2026-06-26T19:31:00Z",
    )

    assert client.tokens == ["yes-token"]
    assert result["markout_records_written"] == 1
    assert result["mean_markout_cents"] == 1.2
    markouts = load_jsonl(tmp_path / "markouts.jsonl")
    assert len(markouts) == 1
    assert markouts[0]["status"] == "marked"
    assert markouts[0]["exit_price"] == 0.041
    assert markouts[0]["entry_price"] == 0.029
    assert markouts[0]["markout_cents"] == 1.2
    assert markouts[0]["city"] == "seoul"
    assert markouts[0]["market_family"] == "temperature"
    assert markouts[0]["bucket_type"] == "ge"
    assert markouts[0]["strategy_id"] == "tail_threshold"
    assert markouts[0]["target_date"] == "2026-06-27"
    assert markouts[0]["end_time"] == "2026-06-27T12:00:00Z"
    assert markouts[0]["settlement_station_code"] == "RKSI"
    assert markouts[0]["settlement_source"] == "metar"
    assert markouts[0]["settlement_rule_hash"] == "rule-seoul-ge-31"
    assert markouts[0]["settlement_spec"]["end_time"] == "2026-06-27T12:00:00Z"
    assert markouts[0]["markout_age_seconds"] == 600
    assert markouts[0]["markout_horizon"] == "5-15m"
    assert markouts[0]["entry_price_bucket"] == "<0.03"
    assert markouts[0]["entry_spread_bucket"] == "0.005-0.015"
    assert markouts[0]["entry_bid_depth_usdc_3c"] == 12.0
    assert markouts[0]["entry_ask_depth_usdc_3c"] == 8.0
    assert markouts[0]["paper_only"] is True


def test_markout_open_paper_fills_skips_recent_marks_when_interval_set(tmp_path):
    write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:21:00Z",
    )
    first_client = FakeClient()
    first = markout_open_paper_fills(
        journal_dir=tmp_path,
        client=first_client,
        recorded_at="2026-06-26T19:31:00Z",
    )
    assert first["markout_records_written"] == 1

    second_client = FakeClient()
    second = markout_open_paper_fills(
        journal_dir=tmp_path,
        client=second_client,
        recorded_at="2026-06-26T19:35:00Z",
        min_markout_interval_seconds=600,
    )

    assert second_client.tokens == []
    assert second["open_fills_seen"] == 0
    assert second["skipped_recent_count"] == 1
    assert second["markout_records_written"] == 0
    assert len(load_jsonl(tmp_path / "markouts.jsonl")) == 1


def test_summarize_markout_strata_groups_latest_marks_with_fill_context(tmp_path):
    write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:21:00Z",
    )
    markout_open_paper_fills(
        journal_dir=tmp_path,
        client=FakeClient(),
        recorded_at="2026-06-26T19:31:00Z",
    )

    summary = summarize_markout_strata(tmp_path)

    assert summary["selected_markout_count"] == 1
    assert summary["mean_markout_cents"] == 1.2
    assert summary["win_rate"] == 1.0
    assert summary["by_horizon"][0]["markout_horizon"] == "5-15m"
    assert summary["by_city"][0]["city"] == "seoul"
    assert summary["by_market_family"][0]["market_family"] == "temperature"
    assert summary["by_bucket_type"][0]["bucket_type"] == "ge"
    assert summary["by_side"][0]["side"] == "yes"
    assert summary["by_strategy"][0]["strategy_id"] == "tail_threshold"
    assert summary["by_station"][0]["settlement_station_code"] == "RKSI"
    assert summary["by_strategy_and_horizon"][0]["strategy_id"] == "tail_threshold"
    assert summary["by_strategy_and_horizon"][0]["markout_horizon"] == "5-15m"
    assert summary["by_station_and_horizon"][0]["settlement_station_code"] == "RKSI"
    assert summary["by_station_and_horizon"][0]["markout_horizon"] == "5-15m"
    assert summary["by_entry_spread_bucket"][0]["entry_spread_bucket"] == "0.005-0.015"


def test_summarize_markout_strata_all_horizons_keeps_path_diagnostics(tmp_path):
    write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:21:00Z",
    )
    markout_open_paper_fills(
        journal_dir=tmp_path,
        client=FakeClient(),
        recorded_at="2026-06-26T19:23:00Z",
    )
    markout_open_paper_fills(
        journal_dir=tmp_path,
        client=FakeClient(),
        recorded_at="2026-06-26T19:31:00Z",
    )

    latest_summary = summarize_markout_strata(tmp_path, latest_only=True)
    path_summary = summarize_markout_strata(tmp_path, latest_only=False)

    assert latest_summary["selected_markout_count"] == 1
    assert path_summary["selected_markout_count"] == 2
    horizons = {
        row["markout_horizon"]: row
        for row in path_summary["by_strategy_and_horizon"]
        if row["strategy_id"] == "tail_threshold"
    }
    assert set(horizons) == {"0-5m", "5-15m"}
    assert horizons["0-5m"]["count"] == 1
    assert horizons["5-15m"]["count"] == 1


def test_summarize_markout_strata_can_filter_near_miss_quarantine_reason(tmp_path):
    write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="quarantine-paper",
        include_candidates=False,
        include_quarantine=True,
        recorded_at="2026-06-26T19:21:00Z",
    )
    markout_open_paper_fills(
        journal_dir=tmp_path,
        client=FakeClient(),
        recorded_at="2026-06-26T19:31:00Z",
    )

    all_summary = summarize_markout_strata(tmp_path)
    risk_only_summary = summarize_markout_strata(
        tmp_path,
        quarantine_reasons=("risk_rule_only_reject",),
    )
    near_miss_summary = summarize_markout_strata(
        tmp_path,
        quarantine_reasons=("single_non_risk_blocker:spread",),
    )

    assert all_summary["marked_count"] == 1
    assert all_summary["by_quarantine_reason"][0]["quarantine_reason"] == "single_non_risk_blocker:spread"
    assert all_summary["by_quarantine_blocker_scope"][0]["quarantine_blocker_scope"] == "single_non_risk_plus_risk"
    assert risk_only_summary["marked_count"] == 0
    assert near_miss_summary["marked_count"] == 1


class NegativeBook:
    token_id = "yes-token"
    market = "0xmarket"
    timestamp = "123"
    best_bid = 0.021
    best_ask = 0.029
    spread = 0.008
    bid_depth_usdc_3c = 12.0
    ask_depth_usdc_3c = 8.0
    bid_levels = 3
    ask_levels = 2


class NegativeClient:
    def get_order_book(self, token_id: str):
        return NegativeBook()


def test_summarize_markout_strata_flags_repeat_negative_groups(tmp_path):
    write_paper_journal(
        _sample_report(),
        journal_dir=tmp_path,
        profile="tail-paper",
        recorded_at="2026-06-26T19:21:00Z",
    )
    for minute in (31, 32, 33):
        markout_open_paper_fills(
            journal_dir=tmp_path,
            client=NegativeClient(),
            recorded_at=f"2026-06-26T19:{minute}:00Z",
        )

    summary = summarize_markout_strata(tmp_path, latest_only=False)

    assert summary["marked_count"] == 3
    assert summary["mean_markout_cents"] == -0.8
    assert any(
        rule["action"] == "do_not_live_until_positive_markout"
        and rule["group"] == "by_horizon"
        for rule in summary["do_not_live_rules"]
    )
