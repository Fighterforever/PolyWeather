from __future__ import annotations

from src.trading.weather_maker_quote_blocker_calibration import (
    build_maker_quote_blocker_calibration_report,
)
from src.trading.weather_paper_journal import _append_jsonl


SPECIFIC_REASON = (
    "negative_markout_rule:maker_quote_by_time_to_expiry_and_spread:"
    "entry_spread_bucket=0.005-0.015,time_to_expiry_bucket=6-24h"
)


def _signal_report():
    return {
        "source_snapshot_id": "scan-1",
        "quarantine": [
            {
                "market_slug": "highest-temperature-in-paris-on-june-27-2026-37corbelow",
                "city": "paris",
                "side": "no",
                "bucket_type": "le",
                "price": 0.17,
                "spread": 0.01,
                "edge_percent": 18.5,
                "would_be_decision_without_risk_rules": "candidate",
                "risk_rule_hits": [SPECIFIC_REASON],
            }
        ],
    }


def _maker_quote_markout(quote_id: str, maker_markout_cents: float):
    return {
        "quote_id": quote_id,
        "fill_id": f"fill-{quote_id}",
        "status": "inferred_filled",
        "entry_spread_bucket": "0.005-0.015",
        "time_to_expiry_bucket": "6-24h",
        "maker_quote_price_bucket": "0.10-0.30",
        "maker_markout_cents": maker_markout_cents,
        "missed_taker_markout_cents": None,
        "bucket_type": "le",
        "city": "paris",
        "market_family": "temperature",
    }


def test_maker_quote_blocker_calibration_keeps_negative_specific_blocker(tmp_path):
    _append_jsonl(
        tmp_path / "maker_quote_markouts.jsonl",
        [
            _maker_quote_markout("q1", -1.0),
            _maker_quote_markout("q2", -2.0),
            _maker_quote_markout("q3", -3.0),
        ],
    )

    report = build_maker_quote_blocker_calibration_report(
        _signal_report(),
        journal_dir=tmp_path,
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "maker_quote_specific_blockers_currently_negative"
    assert report["specific_candidate_blocker_count"] == 1
    assert report["keep_blocked_count"] == 1
    blocker = report["blockers"][0]
    assert blocker["reason"] == SPECIFIC_REASON
    assert blocker["specificity"] == "specific"
    assert blocker["action"] == "keep_blocked_by_maker_quote_evidence"
    assert blocker["evidence"]["count"] == 3
    assert blocker["evidence"]["mean_maker_markout_cents"] == -2.0
    assert "mean_maker_markout_below_threshold" in blocker["failure_reasons"]


def test_maker_quote_blocker_calibration_promotes_positive_specific_blocker_to_review(tmp_path):
    _append_jsonl(
        tmp_path / "maker_quote_markouts.jsonl",
        [
            _maker_quote_markout("q1", 1.0),
            _maker_quote_markout("q2", 2.0),
            _maker_quote_markout("q3", 3.0),
        ],
    )

    report = build_maker_quote_blocker_calibration_report(
        _signal_report(),
        journal_dir=tmp_path,
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "maker_quote_blockers_have_promotable_strata"
    assert report["eligible_blocker_count"] == 1
    assert report["blockers"][0]["action"] == "eligible_for_maker_focus_review"
    assert report["blockers"][0]["failure_reasons"] == []
