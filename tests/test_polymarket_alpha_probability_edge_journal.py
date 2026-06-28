from __future__ import annotations

from src.trading.polymarket_alpha.probability_edge_journal import (
    build_markout_report,
    build_probability_edge_fills_with_snapshots,
    build_resolved_audit_report,
)


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

    assert report["markout_count"] == 3
    assert report["mean_markout_1h"] == 0.2
    assert report["mean_markout_1h_cents"] == 20.0
    assert report["markout_status"] == "forward_markout_positive"
    assert report["live_order_path"] is False


def test_probability_edge_markout_uses_no_token_bid_without_side_flip():
    report = build_markout_report(
        fills=[_fill(side="NO", token_id="no-token", q_effective=0.4)],
        price_rows=[{"token_id": "no-token", "timestamp": "2026-01-01T00:05:00Z", "price_mid": 0.5}],
        horizons=(300,),
    )

    assert report["markouts"][0]["future_side_price"] == 0.5
    assert report["markouts"][0]["markout_cents"] == 10.0


def test_probability_edge_markout_waits_for_probability_edge_fills_when_empty():
    report = build_markout_report(fills=[], price_rows=[])

    assert report["fill_count"] == 0
    assert report["markout_status"] == "ready_waiting_for_valid_crypto_fills"
    assert report["legacy_markout_status"] == "ready_waiting_for_probability_edge_fills"


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


def test_probability_edge_fill_requires_orderbook_snapshot_id():
    journal = build_probability_edge_fills_with_snapshots(
        candidates=[
            {
                "market_slug": "btc-up",
                "token_id": "yes-token",
                "side": "YES",
                "q_effective": 0.4,
                "orderbook_snapshot": {"best_bid": 0.39, "best_ask": 0.4, "bid_ladder": [], "ask_ladder": []},
            }
        ],
        recorded_at="2026-01-01T00:00:00Z",
    )

    assert journal["orderbook_snapshot_id_null_count"] == 0
    assert journal["fills"][0]["orderbook_snapshot_id"] == journal["orderbook_snapshots"][0]["orderbook_snapshot_id"]
    assert journal["fills"][0]["live_order_path"] is False


def test_orderbook_snapshot_written_before_fill():
    journal = build_probability_edge_fills_with_snapshots(
        candidates=[{"market_slug": "btc-up", "token_id": "yes-token", "side": "YES", "q_effective": 0.4}],
        active_markets=[
            {
                "market_slug": "btc-up",
                "token_ids": ["yes-token"],
                "orderbooks": {"yes-token": {"best_bid": 0.39, "best_ask": 0.4, "spread": 0.01, "ask_depth_usdc_3c": 20}},
            }
        ],
        recorded_at="2026-01-01T00:00:00Z",
    )

    assert len(journal["orderbook_snapshots"]) == 1
    assert len(journal["fills"]) == 1
    assert journal["orderbook_snapshots"][0]["source"] == "active_market_orderbook"
