from __future__ import annotations

from src.trading.polymarket_alpha.probability_edge_journal import build_markout_report, build_resolved_audit_report


def _fill(**overrides):
    row = {
        "market_slug": "btc-up",
        "token_id": "yes-token",
        "category": "crypto",
        "side": "YES",
        "model_source": "empirical_bucket_calibration",
        "timestamp": "2026-01-01T00:00:00Z",
        "q_effective": 0.4,
        "paper_only": True,
        "live_order_path": False,
    }
    row.update(overrides)
    return row


def test_probability_edge_markout_reports_forward_price_change():
    report = build_markout_report(
        fills=[_fill()],
        price_rows=[
            {"token_id": "yes-token", "timestamp": "2026-01-01T00:05:00Z", "price_mid": 0.5},
            {"token_id": "yes-token", "timestamp": "2026-01-01T01:00:00Z", "price_mid": 0.6},
        ],
        horizons=(300, 3600),
    )

    assert report["markout_count"] == 2
    assert report["mean_markout_1h"] == 0.2
    assert report["markout_status"] == "forward_markout_positive"
    assert report["live_order_path"] is False


def test_probability_edge_resolved_audit_keeps_unresolved_pnl_null():
    report = build_resolved_audit_report(fills=[_fill()], dataset_rows=[])

    assert report["resolved_fill_count"] == 0
    assert report["resolved_pnl_cents"] is None
    assert report["audits"][0]["resolved_pnl_cents"] is None


def test_probability_edge_resolved_audit_computes_side_pnl_when_resolved():
    report = build_resolved_audit_report(
        fills=[_fill(side="NO", q_effective=0.3)],
        dataset_rows=[{"token_id": "yes-token", "resolved_payout": 0.0}],
    )

    assert report["resolved_fill_count"] == 1
    assert report["resolved_pnl_cents"] == 70.0
    assert report["live_order_path"] is False
