from __future__ import annotations

from src.trading.polymarket_alpha.microstructure_edge_scanner import (
    build_microstructure_markout_report,
    scan_microstructure_edges,
)


def _market(**overrides):
    row = {
        "active": True,
        "category": "sports",
        "market_slug": "match-winner",
        "event_slug": "match",
        "token_id_by_outcome": {"Yes": "yes-token"},
        "orderbooks": {
            "yes-token": {
                "best_bid": 0.42,
                "best_ask": 0.48,
                "spread": 0.06,
                "bid_depth_usdc_3c": 200,
                "ask_depth_usdc_3c": 100,
                "bid_ladder": [],
                "ask_ladder": [],
            }
        },
        "volume": 10000,
        "liquidity": 5000,
    }
    row.update(overrides)
    return row


def test_microstructure_scanner_creates_watch_rows_not_live():
    report = scan_microstructure_edges(
        active_markets=[_market()],
        generated_at="2026-06-30T00:00:00Z",
        min_depth=10,
        min_spread=0.02,
        max_spread=0.1,
    )

    assert report["candidate_count"] == 1
    assert report["paper_fill_count"] == 0
    assert report["watch_rows"][0]["signal_type"] == "spread_capture_watch"
    assert report["watch_rows"][0]["live_order_path"] is False
    assert report["orderbook_snapshots"][0]["live_order_path"] is False


def test_microstructure_scanner_can_emit_configured_paper_fill():
    report = scan_microstructure_edges(
        active_markets=[_market()],
        generated_at="2026-06-30T00:00:00Z",
        min_depth=10,
        min_spread=0.02,
        max_spread=0.1,
        paper_mode="paper_fill",
    )

    assert report["paper_fill_count"] == 1
    assert report["paper_fills"][0]["counts_for_live_gate"] is False


def test_microstructure_markout_tracks_candidate_when_snapshot_available():
    scan = scan_microstructure_edges(
        active_markets=[_market()],
        generated_at="2026-06-30T00:00:00Z",
        min_depth=10,
        min_spread=0.02,
        max_spread=0.1,
    )
    later = {
        **scan["orderbook_snapshots"][0],
        "recorded_at": "2026-06-30T00:05:20Z",
        "timestamp": "2026-06-30T00:05:20Z",
        "best_bid": 0.5,
    }
    report = build_microstructure_markout_report(
        watch_rows=scan["watch_rows"],
        orderbook_snapshots=scan["orderbook_snapshots"] + [later],
    )

    assert report["candidate_count"] == 1
    assert report["available_markout_count"] == 1
    assert report["mean_markout_cents"] == 2.0


def test_microstructure_report_exact_blockers_when_no_candidate():
    report = scan_microstructure_edges(
        active_markets=[_market(orderbooks={})],
        generated_at="2026-06-30T00:00:00Z",
    )

    assert report["candidate_count"] == 0
    assert report["blocker_counts"][0]["reason"] == "missing_orderbook"
    assert report["live_order_path"] is False
