from __future__ import annotations

from src.trading.polymarket_alpha.resolved_join_audit import build_resolved_join_audit_report, classify_missing_outcome


def _market(**overrides):
    row = {
        "market_id": "m1",
        "condition_id": "c1",
        "market_slug": "test-market",
        "event_slug": "test-event",
        "active": False,
        "closed": True,
        "resolved": True,
        "outcomes": ["Yes", "No"],
        "token_ids": ["yes-token", "no-token"],
        "outcome_prices": [1.0, 0.0],
        "category": "crypto",
    }
    row.update(overrides)
    return row


def _snapshot(**overrides):
    row = {
        "market_id": "m1",
        "condition_id": "c1",
        "market_slug": "test-market",
        "event_family_id": "test-event",
        "token_id": "yes-token",
        "outcome_label": "Yes",
        "category": "crypto",
        "decision_time": "2026-01-01T00:00:00Z",
        "resolved_payout": None,
    }
    row.update(overrides)
    return row


def test_resolved_join_audit_reports_missing_outcome_reasons_and_samples():
    report = build_resolved_join_audit_report(
        snapshot_rows=[
            _snapshot(market_slug="active-market", market_id="active", condition_id="active-c", token_id="active-token"),
            _snapshot(market_slug="test-market", token_id="stale-token"),
        ],
        market_rows=[
            _market(market_slug="active-market", market_id="active", condition_id="active-c", token_ids=["active-token"], active=True, closed=False, resolved=False),
            _market(),
        ],
    )

    reasons = {row["reason"]: row["count"] for row in report["missing_outcome_by_market_status"]}
    assert reasons["active_or_unresolved"] == 1
    assert reasons["token_id_mismatch"] == 1
    assert report["sample_rows_by_reason"]["token_id_mismatch"][0]["token_id"] == "stale-token"
    assert report["live_order_path"] is False


def test_resolved_join_audit_counts_unique_resolved_event_families():
    report = build_resolved_join_audit_report(
        snapshot_rows=[
            _snapshot(resolved_payout=1.0),
            _snapshot(token_id="no-token", outcome_label="No", resolved_payout=0.0),
            _snapshot(market_slug="other", market_id="m2", condition_id="c2", event_family_id="other"),
        ],
        market_rows=[_market()],
        manifest={"repair_attempted_count": 3, "repair_success_count": 1},
    )

    assert report["snapshot_row_count"] == 3
    assert report["resolved_snapshot_count"] == 2
    assert report["unique_resolved_event_family_count"] == 1
    assert report["repair_attempted_count"] == 3
    assert report["repair_success_count"] == 1


def test_classify_missing_outcome_closed_without_resolution():
    assert classify_missing_outcome(_snapshot(), _market(resolved=False, outcome_prices=[0.5, 0.5])) == "closed_but_no_resolution"
