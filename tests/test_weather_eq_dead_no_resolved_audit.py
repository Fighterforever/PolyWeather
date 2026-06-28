from __future__ import annotations

from src.trading.weather_eq_dead_no_resolved_audit import build_eq_dead_no_resolved_audit_report


def test_eq_dead_no_resolved_audit_keeps_unresolved_pnl_null():
    report = build_eq_dead_no_resolved_audit_report(
        fills=[{"fill_id": "fill-1", "market_slug": "m", "token_id": "no-token", "entry_price": 0.8}],
        closed_markets=[],
    )

    assert report["resolved_fill_count"] == 0
    assert report["resolved_pnl_cents"] is None
    assert report["rows"][0]["resolved_pnl_cents"] is None


def test_eq_dead_no_resolved_audit_scores_no_winning_token():
    report = build_eq_dead_no_resolved_audit_report(
        fills=[{"fill_id": "fill-1", "market_slug": "m", "token_id": "no-token", "entry_price": 0.8}],
        closed_markets=[
            {
                "market_slug": "m",
                "token_id_by_outcome": {"Yes": "yes-token", "No": "no-token"},
                "settled_yes_payout": 0.0,
            }
        ],
    )

    assert report["resolved_fill_count"] == 1
    assert report["resolved_pnl_cents"] == 20.0
    assert report["rows"][0]["payout"] == 1.0
