from __future__ import annotations

from src.trading.weather_signal_risk_filter import (
    build_risk_rules_from_journal,
    build_risk_rules_from_quarantine_surface,
    fine_time_to_expiry_bucket,
    matching_risk_rules,
    normalize_risk_rules,
    risk_blockers_for_row,
    risk_rules_for_mode,
    row_risk_dimensions,
    time_to_expiry_bucket,
)
from src.trading.weather_paper_journal import _append_jsonl


def test_row_risk_dimensions_bucket_candidate_fields():
    row = {
        "city": "Ankara",
        "bucket_label": "= 28°C",
        "price": 0.58,
        "bid": 0.57,
        "spread": 0.01,
        "edge_percent": 33.2,
    }

    assert row_risk_dimensions(row) == {
        "market_family": "unknown",
        "side": "unknown",
        "city": "ankara",
        "bucket_type": "eq",
        "entry_price_bucket": "0.30-0.60",
        "maker_quote_price_bucket": "0.30-0.60",
        "entry_spread_bucket": "0.005-0.015",
        "entry_edge_bucket": ">=30",
        "time_to_expiry_bucket": "missing",
        "fine_time_to_expiry_bucket": "missing",
    }


def test_time_to_expiry_bucket_uses_utc_end_date():
    assert time_to_expiry_bucket("2026-06-27T06:00:00Z", now="2026-06-27T00:00:00Z") == "<=6h"
    assert time_to_expiry_bucket("2026-06-28T00:00:00Z", now="2026-06-27T00:00:00Z") == "6-24h"
    assert time_to_expiry_bucket("2026-06-29T00:00:00Z", now="2026-06-27T00:00:00Z") == "1-2d"
    assert time_to_expiry_bucket("2026-06-30T00:00:00Z", now="2026-06-27T00:00:00Z") == "2-7d"


def test_fine_time_to_expiry_bucket_splits_coarse_day_windows():
    assert fine_time_to_expiry_bucket("2026-06-27T03:00:00Z", now="2026-06-27T00:00:00Z") == "<=3h"
    assert fine_time_to_expiry_bucket("2026-06-27T06:00:00Z", now="2026-06-27T00:00:00Z") == "3-6h"
    assert fine_time_to_expiry_bucket("2026-06-27T12:00:00Z", now="2026-06-27T00:00:00Z") == "6-12h"
    assert fine_time_to_expiry_bucket("2026-06-28T00:00:00Z", now="2026-06-27T00:00:00Z") == "12-24h"
    assert fine_time_to_expiry_bucket("2026-06-28T12:00:00Z", now="2026-06-27T00:00:00Z") == "24-36h"
    assert fine_time_to_expiry_bucket("2026-06-29T00:00:00Z", now="2026-06-27T00:00:00Z") == "36-48h"


def test_matching_risk_rules_only_uses_do_not_live_rules():
    rules = normalize_risk_rules(
        [
            {
                "action": "collect_more_paper",
                "group": "by_city",
                "dimensions": {"city": "ankara"},
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_city",
                "dimensions": {"city": "ankara"},
                "count": 7,
                "win_rate": 0.0,
                "mean_markout_cents": -1.6,
            },
        ]
    )

    matches = matching_risk_rules({"city": "ankara", "bucket_label": "= 28°C"}, rules)

    assert len(matches) == 1
    assert matches[0]["group"] == "by_city"
    assert risk_blockers_for_row({"city": "ankara", "bucket_label": "= 28°C"}, rules) == [
        "negative_markout_rule:by_city:city=ankara"
    ]


def test_exploration_mode_keeps_specific_rules_but_skips_broad_city_rules():
    rules = normalize_risk_rules(
        [
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_city",
                "dimensions": {"city": "new york"},
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_horizon_and_spread",
                "dimensions": {"markout_horizon": "0-5m", "entry_spread_bucket": "0.015-0.03"},
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_entry_spread_bucket",
                "dimensions": {"entry_spread_bucket": ">0.03"},
            },
        ]
    )

    selected = risk_rules_for_mode(rules, mode="exploration")

    assert [rule["group"] for rule in selected] == [
        "by_horizon_and_spread",
        "by_entry_spread_bucket",
    ]
    assert risk_blockers_for_row(
        {"city": "new york", "bucket_label": "82-83°F", "spread": 0.02},
        rules,
        mode="exploration",
    ) == []
    assert risk_blockers_for_row(
        {"city": "new york", "bucket_label": "82-83°F", "spread": 0.04},
        rules,
        mode="exploration",
    ) == ["negative_markout_rule:by_entry_spread_bucket:entry_spread_bucket=>0.03"]


def test_risk_blockers_deduplicate_same_group_and_dimensions_across_sources():
    rules = normalize_risk_rules(
        [
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_bucket_type",
                "dimensions": {"bucket_type": "eq"},
                "source": "markout_strata",
            },
            {
                "action": "do_not_live_until_positive_markout",
                "group": "by_bucket_type",
                "dimensions": {"bucket_type": "eq"},
                "source": "quarantine_surface",
            },
        ]
    )

    assert risk_blockers_for_row({"bucket_label": "= 28°C"}, rules) == [
        "negative_markout_rule:by_bucket_type:bucket_type=eq"
    ]


def test_build_risk_rules_from_journal_includes_maker_quote_rules(tmp_path):
    _append_jsonl(
        tmp_path / "maker_quote_markouts.jsonl",
        [
            {
                "quote_id": f"quote-{index}",
                "status": "inferred_filled",
                "city": "seoul",
                "bucket_type": "eq",
                "maker_quote_price_bucket": "<0.03",
                "entry_spread_bucket": "<=0.005",
                "time_to_expiry_bucket": "1-2d",
                "maker_markout_cents": -0.1,
            }
            for index in range(3)
        ],
    )

    payload = build_risk_rules_from_journal(journal_dir=tmp_path, min_count=3)

    assert payload["source"] == "markout_and_maker_quote_strata"
    assert payload["min_count"] == 3
    assert payload["maker_quote_summary"]["inferred_fill_count"] == 3
    assert any(rule["group"] == "maker_quote_by_city" for rule in payload["rules"])
    blockers = risk_blockers_for_row(
        {
            "city": "seoul",
            "bucket_label": "= 25°C",
            "price": 0.015,
            "bid": 0.014,
            "spread": 0.001,
        },
        payload["rules"],
    )
    assert "negative_markout_rule:maker_quote_by_city:city=seoul" in blockers


def test_build_risk_rules_from_journal_splits_unactionable_horizon_rules(tmp_path):
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {
                "fill_id": f"fill-{index}",
                "status": "marked",
                "city": "seoul",
                "market_family": "temperature",
                "bucket_type": "eq",
                "entry_spread_bucket": "0.005-0.015",
                "markout_horizon": "1-4h",
                "markout_cents": -1.0,
            }
            for index in range(3)
        ],
    )

    payload = build_risk_rules_from_journal(
        journal_dir=tmp_path,
        min_count=3,
        include_maker_quote_rules=False,
    )

    assert payload["raw_rule_count"] > payload["rule_count"]
    assert payload["unsupported_rule_count"] > 0
    assert "by_horizon" in payload["unsupported_rule_groups"]
    assert "by_horizon_and_spread" in payload["unsupported_rule_groups"]
    assert any(
        "markout_horizon" in rule["unsupported_dimension_keys"]
        for rule in payload["unsupported_rules_sample"]
    )
    assert all(
        "markout_horizon" not in rule["dimensions"]
        for rule in payload["rules"]
    )
    assert any(rule["group"] == "by_city" for rule in payload["rules"])


def test_build_quarantine_surface_rules_from_negative_cooldown_groups(tmp_path):
    quarantine_dir = tmp_path / "quarantine"
    _append_jsonl(
        quarantine_dir / "paper_fills.jsonl",
        [
            {
                "fill_id": "ankara-1",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "city": "ankara",
                "side": "no",
                "bucket_label": "= 28°C",
                "entry_price": 0.58,
                "entry_spread": 0.01,
            },
            {
                "fill_id": "ankara-2",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "city": "ankara",
                "side": "no",
                "bucket_label": "= 29°C",
                "entry_price": 0.62,
                "entry_spread": 0.01,
            },
            {
                "fill_id": "seoul-1",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "city": "seoul",
                "side": "yes",
                "bucket_label": ">= 25°C",
                "entry_price": 0.08,
                "entry_spread": 0.004,
            },
        ],
    )
    _append_jsonl(
        quarantine_dir / "markouts.jsonl",
        [
            {"fill_id": "ankara-1", "status": "marked", "markout_cents": -0.4},
            {"fill_id": "ankara-2", "status": "marked", "markout_cents": -0.2},
            {"fill_id": "seoul-1", "status": "marked", "markout_cents": 2.0},
        ],
    )

    payload = build_risk_rules_from_quarantine_surface(
        paper_journal_dir=tmp_path / "formal",
        quarantine_journal_dir=quarantine_dir,
        min_decision_count=2,
        min_promote_count=2,
    )

    assert payload["source"] == "quarantine_surface_cooldown"
    assert payload["surface_hard_conclusion"] == "quarantine_surface_currently_negative"
    assert payload["rule_count"] > 0
    assert any(
        rule["source"] == "quarantine_surface"
        and rule["group"] == "by_city"
        and rule["dimensions"] == {"city": "ankara"}
        for rule in payload["rules"]
    )
    assert "negative_markout_rule:by_city:city=ankara" in risk_blockers_for_row(
        {
            "city": "ankara",
            "bucket_label": "= 28°C",
            "price": 0.58,
            "spread": 0.01,
        },
        payload["rules"],
    )


def test_build_risk_rules_from_journal_can_include_quarantine_surface_rules(tmp_path):
    formal_dir = tmp_path / "formal"
    quarantine_dir = tmp_path / "quarantine"
    _append_jsonl(
        formal_dir / "paper_fills.jsonl",
        [
            {
                "fill_id": f"formal-{index}",
                "signal_bucket": "candidates",
                "city": "moscow",
                "side": "yes",
                "bucket_label": "= 25°C",
                "entry_price": 0.3,
                "entry_spread": 0.01,
            }
            for index in range(3)
        ],
    )
    _append_jsonl(
        formal_dir / "markouts.jsonl",
        [
            {
                "fill_id": f"formal-{index}",
                "status": "marked",
                "city": "moscow",
                "bucket_type": "eq",
                "entry_spread_bucket": "0.005-0.015",
                "markout_cents": -1.0,
            }
            for index in range(3)
        ],
    )
    _append_jsonl(
        quarantine_dir / "paper_fills.jsonl",
        [
            {
                "fill_id": "ankara-1",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "city": "ankara",
                "side": "no",
                "bucket_label": "= 28°C",
                "entry_price": 0.58,
                "entry_spread": 0.01,
            },
            {
                "fill_id": "ankara-2",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "city": "ankara",
                "side": "no",
                "bucket_label": "= 29°C",
                "entry_price": 0.62,
                "entry_spread": 0.01,
            },
        ],
    )
    _append_jsonl(
        quarantine_dir / "markouts.jsonl",
        [
            {"fill_id": "ankara-1", "status": "marked", "markout_cents": -0.4},
            {"fill_id": "ankara-2", "status": "marked", "markout_cents": -0.2},
        ],
    )

    payload = build_risk_rules_from_journal(
        journal_dir=formal_dir,
        min_count=1,
        include_maker_quote_rules=False,
        include_quarantine_surface_rules=True,
        quarantine_journal_dir=quarantine_dir,
        quarantine_surface_min_decision_count=2,
        quarantine_surface_min_promote_count=2,
    )

    assert payload["source"] == "markout_and_quarantine_surface_strata"
    assert payload["quarantine_surface_summary"]["rule_count"] > 0
    assert any(rule["source"] == "quarantine_surface" for rule in payload["rules"])
    assert any(rule["group"] == "by_city" and rule["dimensions"] == {"city": "moscow"} for rule in payload["rules"])
