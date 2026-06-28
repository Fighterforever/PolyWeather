from __future__ import annotations

from src.trading.weather_threshold_latency_resolved_audit import build_threshold_latency_resolved_audit_report


def _fill():
    return {
        "fill_id": "fill-1",
        "market_slug": "market-1",
        "token_id": "token-yes",
        "entry_price": 0.2,
    }


def test_threshold_latency_resolved_audit_reconstructs_pnl_from_winning_token():
    report = build_threshold_latency_resolved_audit_report(
        fills=[_fill()],
        closed_markets=[{"market_slug": "market-1", "winning_token_id": "token-yes"}],
    )

    assert report["resolved_fill_count"] == 1
    assert report["resolved_pnl_cents"] == 80.0
    assert report["rows"][0]["payout"] == 1.0


def test_threshold_latency_resolved_audit_keeps_unresolved_pnl_null():
    report = build_threshold_latency_resolved_audit_report(fills=[_fill()], closed_markets=[])

    assert report["resolved_fill_count"] == 0
    assert report["unresolved_fill_count"] == 1
    assert report["resolved_pnl_cents"] is None
    assert report["rows"][0]["resolved_pnl_cents"] is None
    assert report["rows"][0]["missing_resolution_reason"] == "missing_closed_market_or_winning_token"
