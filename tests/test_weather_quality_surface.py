from __future__ import annotations

import json

from src.trading.weather_paper_journal import _append_jsonl, write_paper_journal
from src.trading.weather_quality_surface import (
    build_price_conditioned_paper_report,
    build_price_conditioned_quality_search,
    build_price_conditioned_validation_report,
    build_quarantine_promotion_report,
    build_quality_surface_report,
    build_quality_threshold_calibration_report,
    quality_surface_blockers,
)


def _quality_signal_report():
    return {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "generated_at": "2026-06-26T21:00:00Z",
        "source_snapshot_id": "quality-snapshot",
        "source_status": "ready",
        "source": "polymarket_readonly",
        "summary": {
            "candidate_count": 1,
            "watch_count": 0,
            "reject_count": 0,
            "live_gate": False,
            "live_authorization_pct": 0,
        },
        "candidates": [
            {
                "decision": "candidate",
                "row_id": "paris:no",
                "city": "paris",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "token_id": "no-token",
                "side": "no",
                "outcome": "No",
                "bucket_label": "<= 37°C",
                "bucket_type": "le",
                "price": 0.09,
                "bid": 0.08,
                "ask": 0.09,
                "spread": 0.01,
                "liquidity": 62.0,
                "bid_depth_usdc_3c": 47.0,
                "ask_depth_usdc_3c": 62.0,
                "edge_percent": 13.6,
                "model_probability": 0.226,
                "warnings": [],
                "blockers": [],
            }
        ],
    }


def test_quality_surface_blockers_require_non_eq_deep_tradeable_surface():
    blockers = quality_surface_blockers(
        {
            "side": "yes",
            "bucket_label": "= 28°C",
            "entry_price": 0.02,
            "entry_spread": 0.04,
            "entry_liquidity": 1,
            "entry_bid_depth_usdc_3c": 2,
            "entry_ask_depth_usdc_3c": 3,
        },
        allowed_sides=("no",),
    )

    assert blockers == [
        "side_not_allowed",
        "bucket_type_excluded:eq",
        "price_below_min",
        "spread_above_max",
        "liquidity_below_min",
        "bid_depth_below_min",
        "ask_depth_below_min",
    ]


def test_quality_surface_report_summarizes_journal_markout_and_closed_base_rate(tmp_path):
    paper_dir = tmp_path / "paper"
    quarantine_dir = tmp_path / "quarantine"
    backfill_dir = tmp_path / "backfill"
    summary = write_paper_journal(
        _quality_signal_report(),
        journal_dir=paper_dir,
        recorded_at="2026-06-26T21:01:00Z",
    )
    fill_id = summary["run_id"]
    fill = (paper_dir / "paper_fills.jsonl").read_text(encoding="utf-8").splitlines()[0]
    actual_fill_id = json.loads(fill)["fill_id"]
    _append_jsonl(
        paper_dir / "markouts.jsonl",
        [
            {
                "fill_id": actual_fill_id,
                "status": "marked",
                "markout_cents": 1.5,
                "bucket_type": "le",
                "entry_price": 0.09,
                "entry_spread": 0.01,
            }
        ],
    )
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "status": "resolved",
                "city": "paris",
                "bucket_label": "<= 37°C",
                "parsed_temperature_spec": {"comparator": "le"},
                "winning_side": "no",
            }
        ],
    )

    report = build_quality_surface_report(
        paper_journal_dir=paper_dir,
        quarantine_journal_dir=quarantine_dir,
        backfill_dir=backfill_dir,
    )

    assert fill_id
    assert report["counts_for_live_gate"] is False
    assert report["quality_markout_summary"]["quality_surface_count"] == 1
    assert report["quality_markout_summary"]["quality_marked_count"] == 1
    assert report["quality_markout_summary"]["quality_mean_markout_cents"] == 1.5
    assert report["quality_markout_summary"]["by_side"][0]["side"] == "no"
    assert report["closed_base_rate_summary"]["row_count"] == 2
    assert any(
        row["side"] == "no" and row["bucket_type"] == "le" and row["win_rate"] == 1.0
        for row in report["closed_base_rate_summary"]["by_side_and_bucket_type"]
    )


def test_quality_threshold_calibration_recommends_evidence_backed_filter(tmp_path):
    paper_dir = tmp_path / "paper"
    quarantine_dir = tmp_path / "quarantine"
    report = {
        "schema_version": "polyweather_weather_market_signal_report.v1",
        "generated_at": "2026-06-26T21:00:00Z",
        "source_snapshot_id": "threshold-snapshot",
        "source_status": "ready",
        "source": "polymarket_readonly",
        "summary": {
            "candidate_count": 2,
            "watch_count": 0,
            "reject_count": 0,
            "live_gate": False,
            "live_authorization_pct": 0,
        },
        "candidates": [
            {
                "decision": "candidate",
                "row_id": "le:no",
                "city": "paris",
                "market_slug": "le-market",
                "token_id": "le-token",
                "side": "no",
                "bucket_label": "<= 37°C",
                "bucket_type": "le",
                "price": 0.09,
                "bid": 0.08,
                "ask": 0.09,
                "spread": 0.01,
                "liquidity": 60.0,
                "bid_depth_usdc_3c": 40.0,
                "ask_depth_usdc_3c": 40.0,
                "edge_percent": 12.0,
                "model_probability": 0.22,
                "warnings": [],
                "blockers": [],
            },
            {
                "decision": "candidate",
                "row_id": "eq:no",
                "city": "paris",
                "market_slug": "eq-market",
                "token_id": "eq-token",
                "side": "no",
                "bucket_label": "= 32°C",
                "bucket_type": "eq",
                "price": 0.09,
                "bid": 0.08,
                "ask": 0.09,
                "spread": 0.01,
                "liquidity": 60.0,
                "bid_depth_usdc_3c": 40.0,
                "ask_depth_usdc_3c": 40.0,
                "edge_percent": 12.0,
                "model_probability": 0.22,
                "warnings": [],
                "blockers": [],
            },
        ],
    }
    write_summary = write_paper_journal(
        report,
        journal_dir=paper_dir,
        recorded_at="2026-06-26T21:01:00Z",
    )
    fills = [
        json.loads(line)
        for line in (paper_dir / "paper_fills.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    _append_jsonl(
        paper_dir / "markouts.jsonl",
        [
            {
                "fill_id": fills[0]["fill_id"],
                "status": "marked",
                "markout_cents": 1.5,
                "bucket_type": "le",
                "entry_price": 0.09,
                "entry_spread": 0.01,
            },
            {
                "fill_id": fills[1]["fill_id"],
                "status": "marked",
                "markout_cents": -2.5,
                "bucket_type": "eq",
                "entry_price": 0.09,
                "entry_spread": 0.01,
            },
        ],
    )

    calibration = build_quality_threshold_calibration_report(
        paper_journal_dir=paper_dir,
        quarantine_journal_dir=quarantine_dir,
        bucket_type_exclusion_sets=[(), ("eq",)],
        min_price_options=(0.03,),
        max_spread_options=(0.02,),
        min_liquidity_options=(10.0,),
        min_depth_options=(10.0,),
        min_marked_count=1,
        min_mean_markout_cents=0.0,
        min_win_rate=0.55,
    )

    assert write_summary["fill_count"] == 2
    assert calibration["counts_for_live_gate"] is False
    assert calibration["hard_conclusion"] == "threshold_profile_ready_for_paper_review"
    assert calibration["eligible_profile_count"] == 1
    assert calibration["recommended_profile"]["quality_config"]["excluded_bucket_types"] == ["eq"]
    assert calibration["recommended_profile"]["quality_mean_markout_cents"] == 1.5
    assert calibration["recommended_profile"]["quality_win_rate"] == 1.0
    assert any(
        row["quality_config"]["excluded_bucket_types"] == []
        and "mean_markout_below_threshold" in row["failure_reasons"]
        for row in calibration["top_profiles"]
    )


def test_quarantine_promotion_report_requires_forward_and_maker_evidence(tmp_path):
    paper_dir = tmp_path / "paper"
    quarantine_dir = tmp_path / "quarantine"
    fills = []
    markouts = []
    maker_markouts = []
    for index in range(3):
        fill_id = f"risk-fill-{index}"
        fills.append(
            {
                "fill_id": fill_id,
                "status": "open",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "quarantine_blocker_scope": "unknown",
                "city": "paris",
                "market_slug": "le-market",
                "side": "no",
                "bucket_label": "<= 37°C",
                "entry_price": 0.08,
                "entry_spread": 0.01,
            }
        )
        markouts.append(
            {
                "fill_id": fill_id,
                "status": "marked",
                "markout_cents": 1.2 + index,
                "entry_price": 0.08,
                "entry_spread": 0.01,
            }
        )
        maker_markouts.append(
            {
                "quote_id": f"quote-{index}",
                "fill_id": fill_id,
                "status": "inferred_filled",
                "maker_markout_cents": 0.4 + index,
                "quarantine_reason": "risk_rule_only_reject",
                "quarantine_blocker_scope": "unknown",
                "bucket_label": "<= 37°C",
            }
        )
    _append_jsonl(quarantine_dir / "paper_fills.jsonl", fills)
    _append_jsonl(quarantine_dir / "markouts.jsonl", markouts)
    _append_jsonl(quarantine_dir / "maker_quote_markouts.jsonl", maker_markouts)

    report = build_quarantine_promotion_report(
        paper_journal_dir=paper_dir,
        quarantine_journal_dir=quarantine_dir,
        signal_report={
            "quarantine": [
                {
                    "market_slug": "le-market",
                    "city": "paris",
                    "side": "no",
                    "bucket_type": "le",
                    "quarantine_reason": "risk_rule_only_reject",
                    "edge_percent": 12.0,
                    "price": 0.08,
                    "spread": 0.01,
                }
            ]
        },
        min_marked_count=3,
        min_win_rate=0.55,
        min_maker_markout_count=3,
    )

    assert report["counts_for_live_gate"] is False
    assert report["hard_conclusion"] == "quarantine_group_ready_for_formal_paper"
    assert report["promotion_group_count"] >= 1
    reason_group = report["groups"]["by_quarantine_reason"][0]
    assert reason_group["action"] == "promote_to_formal_paper_review"
    assert reason_group["failure_reasons"] == []
    assert report["current_quarantine"]["current_promotable_count"] == 1


def test_price_conditioned_quality_search_marks_risk_only_opportunity_as_quarantine(tmp_path):
    backfill_dir = tmp_path / "backfill"
    _append_jsonl(
        backfill_dir / "closed_markets.jsonl",
        [
            {
                "status": "resolved",
                "city": "paris",
                "bucket_label": "<= 37°C",
                "parsed_temperature_spec": {"comparator": "le"},
                "winning_side": "no",
            }
            for _ in range(30)
        ],
    )
    signal_report = {
        "source_snapshot_id": "signal",
        "assessments": [
            {
                "row_id": "paris:no",
                "decision": "reject",
                "city": "paris",
                "event_title": "Highest temperature in Paris on June 27?",
                "market_id": "2679215",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "question": "Will the highest temperature in Paris be 37°C or below on June 27?",
                "token_id": "no-token",
                "side": "no",
                "outcome": "No",
                "bucket_label": "<= 37°C",
                "bucket_type": "le",
                "price": 0.09,
                "bid": 0.08,
                "ask": 0.09,
                "spread": 0.01,
                "liquidity": 62.0,
                "bid_depth_usdc_3c": 47.0,
                "ask_depth_usdc_3c": 62.0,
                "edge_percent": 13.6,
                "model_probability": 0.226,
                "market_probability": 0.085,
                "source_final_score": 136.0,
                "end_date": "2026-06-27T12:00:00Z",
                "blockers": ["negative_markout_rule:by_entry_price_bucket:entry_price_bucket=0.03-0.10"],
                "risk_rule_hits": ["negative_markout_rule:by_entry_price_bucket:entry_price_bucket=0.03-0.10"],
                "warnings": [],
            }
        ],
    }
    signal_report["assessments"].append(
        {
            **signal_report["assessments"][0],
            "row_id": "paris:no:duplicate-diagnostic-path",
        }
    )

    report = build_price_conditioned_quality_search(
        signal_report,
        backfill_dir=backfill_dir,
        min_base_rate_count=10,
        base_rate_haircut=0.20,
        min_conservative_base_rate=0.55,
    )

    assert report["counts_for_live_gate"] is False
    assert report["opportunity_count"] == 1
    assert report["strict_candidate_count"] == 0
    assert report["risk_only_quarantine_count"] == 1
    opportunity = report["opportunities"][0]
    assert opportunity["price_conditioned_status"] == "risk_only_quarantine"
    assert opportunity["market_id"] == "2679215"
    assert opportunity["token_id"] == "no-token"
    assert opportunity["end_date"] == "2026-06-27T12:00:00Z"
    assert opportunity["conservative_base_rate_fair_price"] > opportunity["entry_price"]
    assert opportunity["price_discount_cents"] > 1.0


def test_price_conditioned_paper_report_is_paper_only_quarantine_shape(tmp_path):
    price_search_report = {
        "report_type": "price_conditioned_quality_search",
        "generated_at": "2026-06-26T21:00:00Z",
        "source_signal_snapshot_id": "signal",
        "opportunity_count": 1,
        "strict_candidate_count": 0,
        "risk_only_quarantine_count": 1,
        "hard_conclusion": "price_conditioned_risk_only_quarantine_found",
        "opportunities": [
            {
                "row_id": "paris:no",
                "decision": "reject",
                "city": "paris",
                "event_title": "Highest temperature in Paris on June 27?",
                "market_id": "2679215",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "question": "Will the highest temperature in Paris be 37°C or below on June 27?",
                "token_id": "no-token",
                "side": "no",
                "outcome": "No",
                "bucket_label": "<= 37°C",
                "bucket_type": "le",
                "entry_price": 0.09,
                "entry_bid": 0.08,
                "entry_ask": 0.09,
                "entry_spread": 0.01,
                "entry_liquidity": 62.0,
                "entry_bid_depth_usdc_3c": 47.0,
                "entry_ask_depth_usdc_3c": 62.0,
                "edge_percent": 13.6,
                "model_probability": 0.226,
                "market_probability": 0.085,
                "source_final_score": 136.0,
                "end_date": "2026-06-27T12:00:00Z",
                "risk_rule_hits": ["negative_markout_rule:by_entry_price_bucket:entry_price_bucket=0.03-0.10"],
                "non_risk_blockers": [],
                "warnings": [],
                "price_conditioned_status": "risk_only_quarantine",
                "base_rate_reference": {"side": "no", "bucket_type": "le", "count": 30},
                "conservative_base_rate_fair_price": 0.63,
                "price_discount_cents": 54.0,
            }
        ],
    }

    report = build_price_conditioned_paper_report(
        price_search_report,
        generated_at="2026-06-26T21:01:00Z",
    )
    summary = write_paper_journal(
        report,
        journal_dir=tmp_path / "price-conditioned",
        include_candidates=True,
        include_watch=True,
        include_quarantine=True,
        recorded_at="2026-06-26T21:01:00Z",
    )

    fill = json.loads(
        (tmp_path / "price-conditioned" / "paper_fills.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert report["summary"]["live_gate"] is False
    assert report["summary"]["live_authorization_pct"] == 0
    assert report["summary"]["quarantine_count"] == 1
    assert report["quarantine"][0]["price"] == 0.09
    assert report["quarantine"][0]["counts_for_live_gate"] is False
    assert summary["quarantine_count"] == 1
    assert fill["market_id"] == "2679215"
    assert fill["token_id"] == "no-token"
    assert fill["end_date"] == "2026-06-27T12:00:00Z"
    assert fill["signal_bucket"] == "quarantine"
    assert fill["counts_for_live_gate"] is False
    assert fill["price_conditioned_status"] == "risk_only_quarantine"
    assert fill["conservative_base_rate_fair_price"] == 0.63
    assert fill["price_discount_cents"] == 54.0


def test_price_conditioned_validation_splits_taker_edge_from_maker_missed_upside(tmp_path):
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {
                "fill_id": f"fill-{index}",
                "status": "marked",
                "bucket_type": "le",
                "side": "no",
                "entry_price_bucket": "0.10-0.30",
                "entry_spread_bucket": "0.005-0.015",
                "markout_cents": 2.0,
            }
            for index in range(5)
        ],
    )
    _append_jsonl(
        tmp_path / "maker_quote_markouts.jsonl",
        [
            {
                "quote_id": f"quote-{index}",
                "status": "resting_unfilled",
                "missed_taker_markout_cents": 2.0,
            }
            for index in range(5)
        ],
    )

    report = build_price_conditioned_validation_report(
        journal_dir=tmp_path,
        min_marked_count=5,
        min_win_rate=0.55,
        min_maker_quote_markouts=3,
        min_maker_inferred_fills=1,
    )

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["taker_entry_evidence"]["status"] == "taker_positive_enough_for_formal_paper_review"
    assert report["maker_execution_evidence"]["status"] == "maker_no_fill_missed_positive_taker"
    assert report["recommended_action"] == "review_taker_only_formal_paper_maker_would_miss"
    assert report["hard_conclusion"] == "price_conditioned_taker_positive_maker_missed_upside"
