from __future__ import annotations

from src.trading.weather_temperature_opportunity import build_temperature_opportunity_report


NEGATIVE_MAKER_REASON = (
    "negative_markout_rule:maker_quote_by_time_to_expiry_and_spread:"
    "entry_spread_bucket=0.005-0.015,time_to_expiry_bucket=6-24h"
)
COLLECT_MORE_REASON = (
    "negative_markout_rule:maker_quote_by_time_to_expiry_and_spread:"
    "entry_spread_bucket=0.015-0.03,time_to_expiry_bucket=6-24h"
)


def test_temperature_opportunity_surfaces_non_eq_negative_maker_blocker():
    signal_report = {
        "source_snapshot_id": "scan-1",
        "candidate_gap_report": {
            "near_candidates": [
                {
                    "decision": "reject",
                    "market_family": "temperature",
                    "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                    "question": "Will the highest temperature in Paris be 37C or below on June 27?",
                    "city": "paris",
                    "side": "no",
                    "bucket_type": "le",
                    "bucket_label": "<= 37C",
                    "price": 0.17,
                    "spread": 0.01,
                    "liquidity": 169.1,
                    "edge_percent": 14.1,
                    "model_probability": 0.311,
                    "market_probability": 0.165,
                    "end_date": "2026-06-27T23:59:00Z",
                    "non_risk_blockers": [],
                    "risk_rule_hits": [NEGATIVE_MAKER_REASON],
                    "would_be_decision_without_risk_rules": "candidate",
                },
                {
                    "decision": "reject",
                    "market_family": "temperature",
                    "market_slug": "highest-temperature-in-paris-on-june-28-2026-31c",
                    "city": "paris",
                    "side": "no",
                    "bucket_type": "eq",
                    "bucket_label": "= 31C",
                    "edge_percent": 20.0,
                    "risk_rule_hits": [],
                },
            ]
        },
    }
    maker_report = {
        "blockers": [
            {
                "reason": NEGATIVE_MAKER_REASON,
                "action": "keep_blocked_by_maker_quote_evidence",
                "evidence": {
                    "count": 9,
                    "inferred_fill_count": 3,
                    "maker_markout_win_rate": 0.0,
                    "mean_maker_markout_cents": -3.0,
                },
                "failure_reasons": ["mean_maker_markout_below_threshold"],
            }
        ]
    }

    report = build_temperature_opportunity_report(
        signal_report,
        maker_quote_blocker_calibration_report=maker_report,
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_temperature_opportunity.v1"
    assert report["hard_conclusion"] == "temperature_opportunity_blocked_by_negative_maker"
    assert report["excluded_bucket_count"] == 1
    assert report["blocked_by_negative_maker_count"] == 1
    assert report["eligible_for_formal_paper_count"] == 0
    row = report["blocked_by_negative_maker_evidence"][0]
    assert row["bucket_type"] == "le"
    assert row["maker_hit_state"] == "negative"
    assert row["maker_hit_details"][0]["evidence"]["mean_maker_markout_cents"] == -3.0


def test_temperature_opportunity_separates_collect_more_from_formal_paper():
    signal_report = {
        "source_snapshot_id": "scan-2",
        "quarantine": [
            {
                "decision": "quarantine",
                "market_family": "temperature",
                "market_slug": "highest-temperature-in-moscow-on-june-27-2026-20corbelow",
                "city": "moscow",
                "side": "yes",
                "bucket_type": "le",
                "bucket_label": "<= 20C",
                "price": 0.44,
                "spread": 0.02,
                "edge_percent": 8.0,
                "non_risk_blockers": [],
                "risk_rule_hits": [COLLECT_MORE_REASON],
                "would_be_decision_without_risk_rules": "candidate",
            },
            {
                "decision": "candidate",
                "market_family": "temperature",
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "city": "paris",
                "side": "no",
                "bucket_type": "le",
                "bucket_label": "<= 37C",
                "price": 0.17,
                "spread": 0.01,
                "edge_percent": 14.1,
                "risk_rule_hits": [],
                "blockers": [],
            },
        ],
    }
    maker_report = {
        "blockers": [
            {
                "reason": COLLECT_MORE_REASON,
                "action": "collect_more_maker_quote_evidence",
                "evidence": {
                    "count": 6,
                    "inferred_fill_count": 0,
                    "fill_inference_rate": 0.0,
                    "mean_maker_markout_cents": None,
                },
                "failure_reasons": ["mean_maker_markout_missing"],
            }
        ]
    }

    report = build_temperature_opportunity_report(
        signal_report,
        maker_quote_blocker_calibration_report=maker_report,
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "temperature_opportunity_has_formal_paper_candidates"
    assert report["eligible_for_formal_paper_count"] == 1
    assert report["collect_more_maker_evidence_count"] == 1
    assert report["collect_more_maker_evidence"][0]["maker_hit_state"] == "insufficient_evidence"
