from __future__ import annotations

from src.trading.weather_paper_journal import _append_jsonl
from src.trading.weather_quarantine_validation import build_quarantine_validation_report


def _markout_rows(city: str, values: list[float]):
    return [
        {
            "fill_id": f"{city}-{index}",
            "status": "marked",
            "city": city,
            "bucket_type": "eq",
            "entry_price_bucket": "<0.03",
            "entry_spread_bucket": "<=0.005",
            "markout_horizon": "15-30m",
            "markout_cents": value,
            "quarantine_reason": "risk_rule_only_reject",
        }
        for index, value in enumerate(values)
    ]


def test_quarantine_validation_flags_positive_quarantine_rule_for_unlock_review(tmp_path):
    paper_dir = tmp_path / "paper"
    quarantine_dir = tmp_path / "quarantine"
    _append_jsonl(paper_dir / "markouts.jsonl", _markout_rows("seoul", [-0.2, -0.3, -0.1]))
    _append_jsonl(
        quarantine_dir / "markouts.jsonl",
        _markout_rows("seoul", [0.2, 0.1, 0.3, 0.4, 0.1]),
    )

    report = build_quarantine_validation_report(
        paper_journal_dir=paper_dir,
        quarantine_journal_dir=quarantine_dir,
        min_unlock_count=5,
        min_unlock_win_rate=0.55,
    )

    assert report["paper_only"] is True
    assert report["counts_for_live_gate"] is False
    assert report["unlock_candidate_count"] >= 1
    assert report["hard_conclusion"] == "review_paper_only_unlock_candidates"
    assert any(
        item["group"] == "by_city"
        and item["dimensions"] == {"city": "seoul"}
        and item["status"] == "eligible_for_unlock_review"
        for item in report["unlock_candidates"]
    )


def test_quarantine_validation_keeps_maker_rule_blocked_without_fills(tmp_path):
    paper_dir = tmp_path / "paper"
    quarantine_dir = tmp_path / "quarantine"
    _append_jsonl(
        paper_dir / "maker_quote_markouts.jsonl",
        [
            {
                "quote_id": f"paper-{index}",
                "status": "resting_unfilled",
                "city": "paris",
                "bucket_type": "eq",
                "maker_quote_price_bucket": "<0.03",
                "entry_spread_bucket": "<=0.005",
                "time_to_expiry_bucket": "1-2d",
                "missed_taker_markout_cents": -0.1,
                "quarantine_reason": "risk_rule_only_reject",
            }
            for index in range(3)
        ],
    )
    _append_jsonl(
        quarantine_dir / "maker_quote_markouts.jsonl",
        [
            {
                "quote_id": f"quarantine-{index}",
                "status": "resting_unfilled",
                "city": "paris",
                "bucket_type": "eq",
                "maker_quote_price_bucket": "<0.03",
                "entry_spread_bucket": "<=0.005",
                "time_to_expiry_bucket": "1-2d",
                "missed_taker_markout_cents": -0.1,
                "quarantine_reason": "risk_rule_only_reject",
            }
            for index in range(5)
        ],
    )

    report = build_quarantine_validation_report(
        paper_journal_dir=paper_dir,
        quarantine_journal_dir=quarantine_dir,
        min_unlock_count=5,
        min_maker_inferred_fills=3,
    )

    assert report["hard_conclusion"] == "keep_current_risk_rules"
    assert report["maker_only_tradeoff"]["protected_no_fill_rule_count"] >= 1
    assert any(
        item["group"] == "maker_quote_by_city"
        and item["dimensions"] == {"city": "paris"}
        and item["status"] == "validated_block_no_fill_protected"
        for item in report["validations"]
    )


def test_quarantine_validation_flags_maker_only_missed_upside(tmp_path):
    paper_dir = tmp_path / "paper"
    quarantine_dir = tmp_path / "quarantine"
    _append_jsonl(
        paper_dir / "maker_quote_markouts.jsonl",
        [
            {
                "quote_id": f"paper-{index}",
                "status": "resting_unfilled",
                "city": "paris",
                "bucket_type": "eq",
                "maker_quote_price_bucket": "<0.03",
                "entry_spread_bucket": "<=0.005",
                "time_to_expiry_bucket": "1-2d",
                "missed_taker_markout_cents": -0.1,
                "quarantine_reason": "risk_rule_only_reject",
            }
            for index in range(3)
        ],
    )
    _append_jsonl(
        quarantine_dir / "maker_quote_markouts.jsonl",
        [
            {
                "quote_id": f"quarantine-{index}",
                "status": "resting_unfilled",
                "city": "paris",
                "bucket_type": "eq",
                "maker_quote_price_bucket": "<0.03",
                "entry_spread_bucket": "<=0.005",
                "time_to_expiry_bucket": "1-2d",
                "missed_taker_markout_cents": 0.2,
                "quarantine_reason": "risk_rule_only_reject",
            }
            for index in range(5)
        ],
    )

    report = build_quarantine_validation_report(
        paper_journal_dir=paper_dir,
        quarantine_journal_dir=quarantine_dir,
        min_unlock_count=5,
        min_maker_inferred_fills=3,
    )

    assert report["maker_only_tradeoff"]["missed_upside_no_fill_rule_count"] >= 1
    assert any(
        item["group"] == "maker_quote_by_city"
        and item["dimensions"] == {"city": "paris"}
        and item["status"] == "validated_block_no_fill_missed_upside"
        for item in report["validations"]
    )
