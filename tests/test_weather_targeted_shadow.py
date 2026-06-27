from __future__ import annotations

from src.trading.weather_paper_journal import _append_jsonl, build_paper_fill_records
from src.trading.weather_targeted_shadow import (
    build_current_signal_taker_probe_report,
    build_current_signal_taker_validation_report,
    build_targeted_shadow_cooldown_report,
    build_targeted_shadow_signal_report,
    build_targeted_shadow_validation_report,
)


def test_targeted_shadow_signal_report_selects_risk_only_and_positive_le_rows():
    report = build_targeted_shadow_signal_report(
        {
            "source_snapshot_id": "scan-1",
            "source_status": "ready",
            "source": "polymarket_readonly",
            "quarantine": [
                {
                    "market_slug": "risk-only",
                    "token_id": "risk-token",
                    "side": "no",
                    "bucket_type": "le",
                    "edge_percent": 10.0,
                    "score": 15,
                    "quarantine_reason": "risk_rule_only_reject",
                },
                {
                    "market_slug": "positive-le",
                    "token_id": "le-token",
                    "side": "yes",
                    "bucket_type": "le",
                    "edge_percent": 4.0,
                    "score": 9,
                    "quarantine_reason": "single_non_risk_blocker:price",
                },
                {
                    "market_slug": "negative-le",
                    "token_id": "neg-token",
                    "side": "yes",
                    "bucket_type": "le",
                    "edge_percent": -3.0,
                    "score": 99,
                    "quarantine_reason": "single_non_risk_blocker:edge",
                },
                {
                    "market_slug": "eq",
                    "token_id": "eq-token",
                    "side": "no",
                    "bucket_type": "eq",
                    "edge_percent": 20.0,
                    "score": 30,
                    "quarantine_reason": "single_non_risk_blocker:bucket_type",
                },
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["counts_for_live_gate"] is False
    assert report["summary"]["targeted_shadow_count"] == 2
    assert [row["market_slug"] for row in report["quarantine"]] == ["risk-only", "positive-le"]
    assert all(row["counts_for_live_gate"] is False for row in report["quarantine"])
    assert report["quarantine"][0]["targeted_shadow_reasons"] == [
        "quarantine_reason:risk_rule_only_reject",
        "bucket_type:le",
    ]


def test_targeted_shadow_signal_report_can_include_negative_edge_when_requested():
    report = build_targeted_shadow_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "negative-le",
                    "token_id": "neg-token",
                    "side": "yes",
                    "bucket_type": "le",
                    "edge_percent": -3.0,
                    "score": 1,
                    "quarantine_reason": "single_non_risk_blocker:edge",
                }
            ]
        },
        positive_edge_only=False,
    )

    assert report["summary"]["targeted_shadow_count"] == 1


def test_targeted_shadow_signal_report_selects_high_edge_near_miss_categories():
    report = build_targeted_shadow_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "high-price",
                    "token_id": "high-price-token",
                    "side": "yes",
                    "bucket_type": "ge",
                    "edge_percent": 18.0,
                    "score": 18,
                    "quarantine_reason": "single_non_risk_blocker:price",
                    "quarantine_non_risk_blocker_categories": ["price"],
                },
                {
                    "market_slug": "low-price",
                    "token_id": "low-price-token",
                    "side": "yes",
                    "bucket_type": "ge",
                    "edge_percent": 2.0,
                    "score": 2,
                    "quarantine_reason": "single_non_risk_blocker:price",
                    "quarantine_non_risk_blocker_categories": ["price"],
                },
                {
                    "market_slug": "high-depth",
                    "token_id": "high-depth-token",
                    "side": "no",
                    "bucket_type": "range",
                    "edge_percent": 12.0,
                    "score": 12,
                    "quarantine_reason": "single_non_risk_blocker:depth",
                    "quarantine_non_risk_blocker_categories": ["depth"],
                },
            ]
        },
        bucket_types=(),
        quarantine_reasons=(),
        near_miss_categories=("price", "depth"),
        min_edge_percent=5.0,
    )

    assert [row["market_slug"] for row in report["quarantine"]] == ["high-price", "high-depth"]
    assert report["quarantine"][0]["targeted_shadow_reasons"] == ["near_miss_category:price"]
    assert report["target_config"]["near_miss_categories"] == ["depth", "price"]
    assert report["target_config"]["min_edge_percent"] == 5.0
    assert report["target_config"]["cooldown_reasons"] == []


def test_targeted_shadow_signal_report_can_require_edge_floor_for_direct_reasons():
    report = build_targeted_shadow_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "low-edge-eq",
                    "token_id": "low-eq-token",
                    "side": "yes",
                    "bucket_type": "eq",
                    "edge_percent": 2.0,
                    "score": 9,
                    "quarantine_reason": "single_non_risk_blocker:bucket_type",
                },
                {
                    "market_slug": "high-edge-eq",
                    "token_id": "high-eq-token",
                    "side": "no",
                    "bucket_type": "eq",
                    "edge_percent": 8.0,
                    "score": 8,
                    "quarantine_reason": "risk_rule_only_reject",
                },
            ]
        },
        quarantine_reasons=("single_non_risk_blocker:bucket_type", "risk_rule_only_reject"),
        bucket_types=("eq",),
        near_miss_categories=("bucket_type",),
        min_edge_percent=5.0,
        require_edge_floor_for_direct_reasons=True,
    )

    assert [row["market_slug"] for row in report["quarantine"]] == ["high-edge-eq"]
    assert report["target_config"]["require_edge_floor_for_direct_reasons"] is True


def test_targeted_shadow_signal_report_suppresses_cooldown_reasons():
    report = build_targeted_shadow_signal_report(
        {
            "quarantine": [
                {
                    "market_slug": "cooldown-le",
                    "token_id": "cooldown-token",
                    "side": "no",
                    "bucket_type": "le",
                    "edge_percent": 12.0,
                    "score": 12,
                    "quarantine_reason": "single_non_risk_blocker:price",
                },
                {
                    "market_slug": "active-price",
                    "token_id": "active-token",
                    "side": "yes",
                    "bucket_type": "ge",
                    "edge_percent": 10.0,
                    "score": 10,
                    "quarantine_reason": "single_non_risk_blocker:price",
                    "quarantine_non_risk_blocker_categories": ["price"],
                },
            ]
        },
        bucket_types=("le",),
        quarantine_reasons=(),
        near_miss_categories=("price",),
        cooldown_reasons=("bucket_type:le",),
        min_edge_percent=5.0,
    )

    assert [row["market_slug"] for row in report["quarantine"]] == ["active-price"]
    assert report["summary"]["targeted_shadow_count"] == 1
    assert report["summary"]["targeted_shadow_suppressed_count"] == 1
    assert report["suppressed"][0]["market_slug"] == "cooldown-le"
    assert report["suppressed"][0]["targeted_shadow_cooldown_reasons"] == ["bucket_type:le"]


def test_targeted_shadow_fields_are_preserved_in_paper_fill_records():
    report = build_targeted_shadow_signal_report(
        {
            "source_snapshot_id": "scan-1",
            "quarantine": [
                {
                    "market_slug": "risk-only",
                    "token_id": "risk-token",
                    "side": "no",
                    "bucket_type": "le",
                    "edge_percent": 10.0,
                    "score": 15,
                    "quarantine_reason": "risk_rule_only_reject",
                    "price": 0.12,
                }
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    fills = build_paper_fill_records(
        report,
        include_candidates=False,
        include_quarantine=True,
        recorded_at="2026-06-27T00:01:00Z",
    )

    assert fills[0]["targeted_shadow"] is True
    assert fills[0]["targeted_shadow_reasons"] == [
        "quarantine_reason:risk_rule_only_reject",
        "bucket_type:le",
    ]
    assert fills[0]["counts_for_live_gate"] is False


def test_current_signal_taker_probe_selects_candidate_like_quarantine_rows():
    report = build_current_signal_taker_probe_report(
        {
            "source_snapshot_id": "scan-1",
            "source_status": "ready",
            "source": "polymarket_readonly",
            "quarantine": [
                {
                    "market_slug": "risk-only",
                    "token_id": "risk-token",
                    "side": "no",
                    "outcome": "No",
                    "bucket_type": "le",
                    "bucket_label": "<= 37°C",
                    "price": 0.19,
                    "ask": 0.19,
                    "bid": 0.18,
                    "spread": 0.01,
                    "edge_percent": 12.0,
                    "score": 12,
                    "quarantine_reason": "risk_rule_only_reject",
                    "would_be_decision_without_risk_rules": "candidate",
                    "risk_rule_hits": ["negative_markout_rule:x"],
                },
                {
                    "market_slug": "wide",
                    "token_id": "wide-token",
                    "side": "yes",
                    "bucket_type": "ge",
                    "price": 0.20,
                    "ask": 0.20,
                    "spread": 0.20,
                    "edge_percent": 20.0,
                    "quarantine_reason": "risk_rule_only_reject",
                    "would_be_decision_without_risk_rules": "candidate",
                },
                {
                    "market_slug": "watch-like",
                    "token_id": "watch-token",
                    "side": "yes",
                    "bucket_type": "ge",
                    "price": 0.20,
                    "ask": 0.20,
                    "spread": 0.01,
                    "edge_percent": 20.0,
                    "quarantine_reason": "risk_rule_only_reject",
                    "would_be_decision_without_risk_rules": "watch",
                },
            ],
        },
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["schema_version"] == "polyweather_weather_current_signal_taker.v1"
    assert report["counts_for_live_gate"] is False
    assert report["summary"]["current_signal_taker_count"] == 1
    assert report["summary"]["rejected_probe_count"] == 2
    assert report["hard_conclusion"] == "current_signal_taker_collect_paper"
    row = report["quarantine"][0]
    assert row["market_slug"] == "risk-only"
    assert row["execution_mode"] == "taker_cross"
    assert row["current_signal_taker_probe"] is True
    assert row["counts_for_live_gate"] is False
    assert "current_signal_taker_not_live_calibrated" in row["blockers"]


def test_current_signal_taker_probe_can_restrict_to_tail_bucket_types():
    report = build_current_signal_taker_probe_report(
        {
            "source_snapshot_id": "scan-tail-only",
            "source_status": "ready",
            "source": "polymarket_readonly",
            "quarantine": [
                {
                    "market_slug": "exact-bin",
                    "token_id": "eq-token",
                    "side": "yes",
                    "bucket_type": "eq",
                    "bucket_label": "37°C",
                    "price": 0.18,
                    "ask": 0.18,
                    "spread": 0.01,
                    "edge_percent": 30.0,
                    "score": 30,
                    "quarantine_reason": "risk_rule_only_reject",
                    "would_be_decision_without_risk_rules": "candidate",
                },
                {
                    "market_slug": "tail-bin",
                    "token_id": "tail-token",
                    "side": "no",
                    "bucket_type": "le",
                    "bucket_label": "<= 37°C",
                    "price": 0.19,
                    "ask": 0.19,
                    "spread": 0.01,
                    "edge_percent": 12.0,
                    "score": 12,
                    "quarantine_reason": "risk_rule_only_reject",
                    "would_be_decision_without_risk_rules": "candidate",
                },
            ],
        },
        allowed_bucket_types=("le", "ge"),
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["config"]["allowed_bucket_types"] == ["ge", "le"]
    assert report["summary"]["current_signal_taker_count"] == 1
    assert report["summary"]["rejected_probe_count"] == 1
    assert report["quarantine"][0]["market_slug"] == "tail-bin"
    assert report["rejected_probe_rows"][0]["market_slug"] == "exact-bin"
    assert report["rejected_probe_rows"][0]["failure_reasons"] == ["bucket_type_not_allowed"]


def test_current_signal_taker_validation_reports_horizon_blockers(tmp_path):
    _append_jsonl(
        tmp_path / "paper_fills.jsonl",
        [
            {
                "fill_id": "fill-1",
                "status": "open",
                "signal_bucket": "quarantine",
                "quarantine_reason": "risk_rule_only_reject",
                "recorded_at": "2026-06-27T00:00:00Z",
            }
        ],
    )
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {
                "fill_id": "fill-1",
                "status": "marked",
                "markout_cents": 1.0,
                "markout_horizon": "0-5m",
            }
        ],
    )
    _append_jsonl(
        tmp_path / "resolved_audits.jsonl",
        [
            {
                "fill_id": "fill-1",
                "status": "unresolved",
            }
        ],
    )

    report = build_current_signal_taker_validation_report(
        journal_dir=tmp_path,
        min_marked_count=2,
        min_horizon_count=2,
        min_resolved_count=1,
    )

    assert report["counts_for_live_gate"] is False
    assert report["summary"]["marked_count"] == 1
    assert report["summary"]["resolved_count"] == 0
    assert report["hard_conclusion"] == "current_signal_taker_validation_collect_more_markouts"
    assert "insufficient_marked_count_1_of_2" in report["blockers"]
    assert "required_horizon_samples_missing" in report["blockers"]
    assert "insufficient_resolved_count_0_of_1" in report["blockers"]


def test_targeted_shadow_validation_promotes_only_evidence_backed_groups(tmp_path):
    fills = [
        {
            "fill_id": "fill-1",
            "status": "open",
            "market_slug": "m1",
            "token_id": "t1",
            "side": "no",
            "bucket_label": "<= 37°C",
            "targeted_shadow": True,
            "targeted_shadow_reasons": ["bucket_type:le"],
        },
        {
            "fill_id": "fill-2",
            "status": "open",
            "market_slug": "m2",
            "token_id": "t2",
            "side": "no",
            "bucket_label": "<= 38°C",
            "targeted_shadow": True,
            "targeted_shadow_reasons": ["bucket_type:le"],
        },
    ]
    _append_jsonl(tmp_path / "paper_fills.jsonl", fills)
    _append_jsonl(
        tmp_path / "markouts.jsonl",
        [
            {"fill_id": "fill-1", "status": "marked", "markout_cents": 1.0},
            {"fill_id": "fill-2", "status": "marked", "markout_cents": 2.0},
        ],
    )
    _append_jsonl(
        tmp_path / "maker_quotes.jsonl",
        [
            {"quote_id": "q1", "fill_id": "fill-1"},
            {"quote_id": "q2", "fill_id": "fill-2"},
        ],
    )
    _append_jsonl(
        tmp_path / "maker_quote_markouts.jsonl",
        [
            {"quote_id": "q1", "fill_id": "fill-1", "status": "inferred_filled", "maker_markout_cents": 0.2},
            {"quote_id": "q2", "fill_id": "fill-2", "status": "inferred_filled", "maker_markout_cents": 0.4},
        ],
    )

    report = build_targeted_shadow_validation_report(
        journal_dir=tmp_path,
        min_marked_count=2,
        min_win_rate=0.55,
        min_maker_inferred_fills=2,
    )

    assert report["counts_for_live_gate"] is False
    assert report["hard_conclusion"] == "targeted_shadow_ready_for_formal_paper_review"
    assert report["ready_group_count"] == 1
    assert report["ready_groups"][0]["targeted_shadow_reason"] == "bucket_type:le"
    assert report["ready_groups"][0]["action"] == "promote_to_formal_paper_review"


def test_targeted_shadow_cooldown_report_flags_negative_evidence_groups():
    report = build_targeted_shadow_cooldown_report(
        {
            "hard_conclusion": "targeted_shadow_collect_more_or_reject",
            "groups": [
                {
                    "targeted_shadow_reason": "bucket_type:le",
                    "marked_count": 2,
                    "mean_markout_cents": -0.8,
                    "win_rate": 0.0,
                },
                {
                    "targeted_shadow_reason": "near_miss_category:price",
                    "marked_count": 1,
                    "mean_markout_cents": -0.1,
                    "win_rate": 0.0,
                },
                {
                    "targeted_shadow_reason": "near_miss_category:depth",
                    "marked_count": 3,
                    "mean_markout_cents": 0.2,
                    "win_rate": 0.67,
                },
            ],
        },
        min_marked_count=2,
        min_mean_markout_cents=0.0,
        max_win_rate=0.5,
        generated_at="2026-06-27T00:00:00Z",
    )

    assert report["hard_conclusion"] == "targeted_shadow_cooldown_active"
    assert report["cooldown_reasons"] == ["bucket_type:le"]
    assert report["groups"][0]["action"] == "cooldown_targeted_shadow_reason"
    assert report["counts_for_live_gate"] is False
