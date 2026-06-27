from __future__ import annotations

from src.trading.weather_execution_calibration import (
    build_maker_focus_signal_report,
    build_execution_calibration_records,
    summarize_execution_calibration,
)
from src.trading.weather_paper_journal import _append_jsonl


def test_execution_calibration_splits_taker_midpoint_and_maker_assumptions(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            {
                "fill_id": "fill-1",
                "status": "open",
                "city": "new york",
                "market_slug": "nyc-weather",
                "token_id": "yes-token",
                "side": "yes",
                "outcome": "Yes",
                "bucket_label": "86-87°F",
                "entry_price": 0.03,
                "entry_bid": 0.02,
                "entry_ask": 0.03,
                "entry_spread": 0.01,
            }
        ],
    )
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {
                "markout_id": "markout-1",
                "fill_id": "fill-1",
                "status": "marked",
                "city": "new york",
                "bucket_label": "86-87°F",
                "bucket_type": "range",
                "markout_horizon": "5-15m",
                "entry_price": 0.03,
                "entry_bid": 0.02,
                "entry_ask": 0.03,
                "entry_spread": 0.01,
                "entry_spread_bucket": "0.005-0.015",
                "current_bid": 0.025,
                "current_ask": 0.035,
                "markout_cents": -0.5,
            }
        ],
    )

    records = build_execution_calibration_records(journal_dir=tmp_path)

    by_mode = {record["execution_mode"]: record for record in records}
    assert set(by_mode) == {"taker_ask", "midpoint", "maker_bid"}
    assert by_mode["taker_ask"]["markout_cents"] == -0.5
    assert by_mode["midpoint"]["markout_cents"] == 0.0
    assert by_mode["maker_bid"]["markout_cents"] == 0.5
    assert by_mode["taker_ask"]["hypothetical_fill"] is False
    assert by_mode["maker_bid"]["hypothetical_fill"] is True
    assert all(record["counts_for_live_gate"] is False for record in records)


def test_execution_calibration_summary_flags_when_spread_cost_explains_negative_taker(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            {
                "fill_id": "fill-1",
                "status": "open",
                "city": "new york",
                "entry_price": 0.03,
                "entry_bid": 0.02,
                "entry_ask": 0.03,
                "entry_spread": 0.01,
            }
        ],
    )
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {
                "markout_id": "markout-1",
                "fill_id": "fill-1",
                "status": "marked",
                "entry_price": 0.03,
                "entry_bid": 0.02,
                "entry_ask": 0.03,
                "entry_spread": 0.01,
                "current_bid": 0.025,
            }
        ],
    )

    summary = summarize_execution_calibration(tmp_path, generated_at="2026-06-27T00:00:00Z")

    assert summary["record_count"] == 3
    assert summary["fill_count"] == 1
    assert summary["live_gate_input"] is False
    stats = {row["execution_mode"]: row for row in summary["by_execution_mode"]}
    assert stats["taker_ask"]["mean_markout_cents"] == -0.5
    assert stats["midpoint"]["mean_markout_cents"] == 0.0
    assert stats["maker_bid"]["mean_markout_cents"] == 0.5
    assert summary["execution_delta"]["spread_cost_explains_current_negative_markout"] is True
    assert summary["execution_delta"]["maker_bid_improvement_vs_taker_cents"] == 1.0


def test_maker_focus_signal_report_selects_current_rows_matching_positive_maker_strata():
    report = build_maker_focus_signal_report(
        {
            "source_snapshot_id": "scan-1",
            "source_status": "ready",
            "source": "polymarket_readonly",
            "quarantine": [
                {
                    "market_slug": "wide-enough",
                    "token_id": "token-1",
                    "side": "no",
                    "bucket_label": "= 31°C",
                    "bucket_type": "eq",
                    "price": 0.64,
                    "bid": 0.62,
                    "ask": 0.64,
                    "spread": 0.02,
                    "edge_percent": 30.0,
                    "score": 100.0,
                    "quarantine_reason": "single_non_risk_blocker:bucket_type",
                },
                {
                    "market_slug": "too-tight",
                    "token_id": "token-2",
                    "side": "no",
                    "bucket_label": "= 29°C",
                    "bucket_type": "eq",
                    "price": 0.63,
                    "bid": 0.62,
                    "ask": 0.63,
                    "spread": 0.01,
                    "edge_percent": 25.0,
                    "score": 90.0,
                    "quarantine_reason": "single_non_risk_blocker:bucket_type",
                },
            ],
        },
        {
            "by_execution_and_spread": [
                {
                    "execution_mode": "maker_bid",
                    "entry_spread_bucket": "0.015-0.03",
                    "count": 3,
                    "hypothetical_count": 3,
                    "mean_markout_cents": 2.0,
                    "win_rate": 0.666667,
                },
                {
                    "execution_mode": "maker_bid",
                    "entry_spread_bucket": "0.005-0.015",
                    "count": 5,
                    "hypothetical_count": 5,
                    "mean_markout_cents": -0.18,
                    "win_rate": 0.2,
                },
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "maker_focus_collect_formal_paper"
    assert report["eligible_group_count"] == 1
    assert report["summary"]["maker_focus_count"] == 1
    selected = report["quarantine"][0]
    assert selected["market_slug"] == "wide-enough"
    assert selected["price"] == 0.62
    assert selected["maker_focus_reference_ask"] == 0.64
    assert selected["maker_focus_reasons"] == [
        "maker_positive_stratum:entry_spread_bucket=0.015-0.03"
    ]
    assert selected["counts_for_live_gate"] is False


def test_maker_focus_signal_report_suppresses_rows_without_maker_bid():
    report = build_maker_focus_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "missing-bid",
                    "token_id": "token-1",
                    "side": "no",
                    "price": 0.64,
                    "ask": 0.64,
                    "spread": 0.02,
                    "edge_percent": 30.0,
                }
            ]
        },
        {
            "by_execution_and_spread": [
                {
                    "execution_mode": "maker_bid",
                    "entry_spread_bucket": "0.015-0.03",
                    "count": 3,
                    "mean_markout_cents": 2.0,
                    "win_rate": 0.666667,
                }
            ],
        },
    )

    assert report["summary"]["maker_focus_count"] == 0
    assert report["summary"]["maker_focus_suppressed_count"] == 1
    assert report["suppressed"][0]["maker_focus_suppression_reason"] == "missing_bid_for_maker_entry"


def test_maker_focus_signal_report_respects_maker_quote_negative_risk_rules():
    report = build_maker_focus_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "maker-risk-blocked",
                    "token_id": "token-1",
                    "side": "no",
                    "price": 0.64,
                    "bid": 0.62,
                    "ask": 0.64,
                    "spread": 0.02,
                    "edge_percent": 30.0,
                    "risk_rule_hits": [
                        "negative_markout_rule:maker_quote_by_market_family:market_family=temperature",
                        "negative_markout_rule:maker_quote_by_entry_spread_bucket:entry_spread_bucket=0.015-0.03",
                        "negative_markout_rule:maker_quote_by_time_to_expiry_and_spread:entry_spread_bucket=0.015-0.03,time_to_expiry_bucket=6-24h",
                    ],
                }
            ]
        },
        {
            "by_execution_and_spread": [
                {
                    "execution_mode": "maker_bid",
                    "entry_spread_bucket": "0.015-0.03",
                    "count": 3,
                    "mean_markout_cents": 2.0,
                    "win_rate": 0.666667,
                }
            ],
        },
    )

    assert report["summary"]["maker_focus_count"] == 0
    assert report["summary"]["maker_focus_suppressed_count"] == 1
    assert report["summary"]["maker_quote_specific_risk_suppressed_count"] == 1
    assert report["summary"]["maker_quote_broad_only_risk_suppressed_count"] == 0
    assert report["hard_conclusion"] == "maker_focus_current_matches_specific_risk_suppressed"
    assert report["suppressed"][0]["maker_focus_suppression_reason"] == "maker_quote_risk_rule_block"
    assert report["suppressed"][0]["maker_focus_suppression_risk_hits_broad"] == [
        "negative_markout_rule:maker_quote_by_market_family:market_family=temperature"
    ]
    assert report["suppressed"][0]["maker_focus_suppression_risk_hits_medium"] == [
        "negative_markout_rule:maker_quote_by_entry_spread_bucket:entry_spread_bucket=0.015-0.03"
    ]
    assert report["suppressed"][0]["maker_focus_suppression_risk_hits_specific"] == [
        "negative_markout_rule:maker_quote_by_time_to_expiry_and_spread:entry_spread_bucket=0.015-0.03,time_to_expiry_bucket=6-24h"
    ]


def test_maker_focus_signal_report_reports_broad_only_maker_quote_suppression():
    report = build_maker_focus_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "broad-only",
                    "token_id": "token-1",
                    "side": "no",
                    "price": 0.64,
                    "bid": 0.62,
                    "ask": 0.64,
                    "spread": 0.02,
                    "edge_percent": 30.0,
                    "risk_rule_hits": [
                        "negative_markout_rule:maker_quote_by_market_family:market_family=temperature"
                    ],
                }
            ]
        },
        {
            "by_execution_and_spread": [
                {
                    "execution_mode": "maker_bid",
                    "entry_spread_bucket": "0.015-0.03",
                    "count": 3,
                    "mean_markout_cents": 2.0,
                    "win_rate": 0.666667,
                }
            ],
        },
    )

    assert report["summary"]["maker_focus_count"] == 0
    assert report["summary"]["maker_quote_specific_risk_suppressed_count"] == 0
    assert report["summary"]["maker_quote_medium_only_risk_suppressed_count"] == 0
    assert report["summary"]["maker_quote_broad_only_risk_suppressed_count"] == 1
    assert report["hard_conclusion"] == "maker_focus_current_matches_suppressed"
    assert report["suppressed"][0]["maker_focus_suppression_risk_hits_broad"] == [
        "negative_markout_rule:maker_quote_by_market_family:market_family=temperature"
    ]
