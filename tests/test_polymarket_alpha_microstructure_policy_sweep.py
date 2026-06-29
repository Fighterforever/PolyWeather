from __future__ import annotations

from src.trading.polymarket_alpha.microstructure_policy_sweep import (
    build_microstructure_followup_snapshots,
    build_microstructure_policy_markout_report,
    build_microstructure_policy_sweep,
)


def _watch(**overrides):
    row = {
        "watch_id": "watch-1",
        "market_slug": "market-1",
        "event_slug": "event-1",
        "category": "sports",
        "token_id": "yes-token",
        "side": "YES",
        "entry_time": "2026-06-30T00:00:00Z",
        "best_bid": 0.42,
        "best_ask": 0.48,
        "q_effective": 0.48,
        "bid_depth": 300,
        "ask_depth": 100,
        "spread": 0.06,
        "depth_imbalance": 0.5,
        "orderbook_snapshot_id": "snap-1",
        "paper_only": True,
        "live_order_path": False,
    }
    row.update(overrides)
    return row


def _market(include_no: bool = True):
    orderbooks = {
        "yes-token": {"best_bid": 0.42, "best_ask": 0.48, "spread": 0.06, "bid_depth_usdc_3c": 300, "ask_depth_usdc_3c": 100},
    }
    token_map = {"Yes": "yes-token"}
    if include_no:
        orderbooks["no-token"] = {"best_bid": 0.50, "best_ask": 0.56, "spread": 0.06, "bid_depth_usdc_3c": 100, "ask_depth_usdc_3c": 100}
        token_map["No"] = "no-token"
    return {"active": True, "market_slug": "market-1", "token_id_by_outcome": token_map, "orderbooks": orderbooks}


def test_policy_sweep_accounts_for_watch_rows_and_separates_taker_maker():
    report = build_microstructure_policy_sweep(
        watch_rows=[_watch(depth_imbalance=0.7)],
        active_markets=[_market()],
        imbalance_threshold=0.55,
        min_depth=25,
    )

    assert report["input_watch_count"] == 1
    assert report["policy_variant_count"] == 5
    assert report["taker_fill_count"] == 2
    assert report["maker_quote_count"] == 1
    assert {row["policy_id"] for row in report["candidates"]} == {"taker_follow_imbalance", "taker_inverse_imbalance", "maker_spread_capture"}
    assert all(row["live_order_path"] is False for row in report["candidates"])


def test_policy_sweep_buy_no_requires_direct_no_book():
    report = build_microstructure_policy_sweep(
        watch_rows=[_watch(depth_imbalance=-0.8)],
        active_markets=[_market(include_no=False)],
        imbalance_threshold=0.55,
    )

    blockers = {row["reason"]: row["count"] for row in report["blocker_counts"]}
    assert blockers["taker_follow_imbalance:missing_no_token"] == 1
    assert report["inverse_candidate_count"] == 1
    assert report["taker_fill_count"] == 1
    assert report["maker_quote_count"] == 1


def test_policy_followup_snapshots_track_fills_quotes_and_recent_watch_rows():
    sweep = build_microstructure_policy_sweep(
        watch_rows=[_watch(depth_imbalance=0.7)],
        active_markets=[_market()],
    )
    report = build_microstructure_followup_snapshots(
        active_markets=[_market()],
        taker_fills=sweep["taker_fills"],
        maker_quotes=sweep["maker_quotes"],
        watch_rows=[_watch()],
        recorded_at="2026-06-30T00:05:00Z",
    )

    assert report["snapshot_count"] == 4
    assert all(row["live_order_path"] is False for row in report["snapshots"])


def test_policy_markout_reports_quote_not_filled_and_taker_markout():
    sweep = build_microstructure_policy_sweep(
        watch_rows=[_watch(depth_imbalance=0.7)],
        active_markets=[_market()],
    )
    followup = build_microstructure_followup_snapshots(
        active_markets=[_market()],
        taker_fills=sweep["taker_fills"],
        maker_quotes=sweep["maker_quotes"],
        recorded_at="2026-06-30T00:05:00Z",
    )
    report = build_microstructure_policy_markout_report(
        taker_fills=sweep["taker_fills"],
        maker_quotes=sweep["maker_quotes"],
        orderbook_snapshots=followup["snapshots"],
    )

    assert report["taker_fill_count"] == 2
    assert report["maker_quote_count"] == 1
    assert report["available_markout_count"] == 2
    reasons = {row["reason"]: row["count"] for row in report["missing_snapshot_reason_counts"]}
    assert reasons["quote_not_filled"] == 1
