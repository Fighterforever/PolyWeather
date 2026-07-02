from __future__ import annotations

from src.trading.polymarket_alpha.probability_edge_journal import (
    build_formal_fill_followup_orderbook_snapshots,
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


def test_crypto_touch_formal_fills_counted_for_markout():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["timestamp"] = "2026-06-29T00:00:00Z"
    report = build_markout_report(fills=[fill], price_rows=[], orderbook_snapshots=[], horizons=(300,))

    assert report["fill_count"] == 1
    assert report["available_markout_count"] == 0
    assert report["markouts"][0]["missing_snapshot_reason"] == "missing_snapshot_for_horizon"
    assert report["markout_status"] == "missing_later_snapshot"


def test_markout_uses_timestamp_as_entry_time_fallback():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["timestamp"] = "2026-06-29T00:00:00Z"
    report = build_markout_report(
        fills=[fill],
        price_rows=[{"token_id": "yes-token", "timestamp": "2026-06-29T00:05:00Z", "price_mid": 0.25}],
        orderbook_snapshots=[],
        horizons=(300,),
    )

    assert report["fill_count"] == 1
    assert report["available_markout_count"] == 2
    assert report["markouts"][0]["entry_time"] == "2026-06-29T00:00:00Z"


def test_missing_later_snapshot_does_not_hide_fill_count():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["entry_time"] = "2026-06-29T00:00:00Z"
    report = build_markout_report(fills=[fill], price_rows=[], orderbook_snapshots=[], horizons=(300, 900))

    assert report["fill_count"] == 1
    assert report["markout_count"] == 3
    assert report["available_markout_count"] == 0
    reasons = {row["reason"]: row["count"] for row in report["missing_snapshot_reason_counts"]}
    assert reasons["missing_snapshot_for_horizon"] == 2
    assert reasons["missing_later_snapshot"] == 1


def test_markout_uses_horizon_tolerance():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["entry_time"] = "2026-06-29T00:00:00Z"
    report = build_markout_report(
        fills=[fill],
        price_rows=[
            {"token_id": "yes-token", "timestamp": "2026-06-29T00:04:20Z", "price_mid": 0.24},
        ],
        horizons=(300,),
    )

    five_min = next(row for row in report["markouts"] if row["horizon_seconds"] == 300)
    assert five_min["horizon_match_status"] == "within_tolerance"
    assert five_min["target_time"] == "2026-06-29T00:05:00Z"
    assert five_min["matched_snapshot_time"] == "2026-06-29T00:04:20Z"
    assert five_min["match_lag_seconds"] == -40.0


def test_markout_does_not_reuse_1h_snapshot_for_5m():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["entry_time"] = "2026-06-29T00:00:00Z"
    report = build_markout_report(
        fills=[fill],
        price_rows=[
            {"token_id": "yes-token", "timestamp": "2026-06-29T01:00:00Z", "price_mid": 0.24},
        ],
        horizons=(300, 3600),
    )

    five_min = next(row for row in report["markouts"] if row["horizon_seconds"] == 300)
    one_hour = next(row for row in report["markouts"] if row["horizon_seconds"] == 3600)
    assert five_min["markout_cents"] is None
    assert five_min["horizon_match_status"] == "missing_snapshot_for_horizon"
    assert one_hour["horizon_match_status"] == "exact"
    assert one_hour["matched_snapshot_time"] == "2026-06-29T01:00:00Z"


def test_current_markout_uses_latest_snapshot_only_for_current():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["entry_time"] = "2026-06-29T00:00:00Z"
    report = build_markout_report(
        fills=[fill],
        price_rows=[
            {"token_id": "yes-token", "timestamp": "2026-06-29T00:01:00Z", "price_mid": 0.21},
            {"token_id": "yes-token", "timestamp": "2026-06-29T01:00:00Z", "price_mid": 0.25},
        ],
        horizons=(60,),
    )

    one_min = next(row for row in report["markouts"] if row["horizon_seconds"] == 60)
    current = next(row for row in report["markouts"] if row["horizon_seconds"] == 0)
    assert one_min["horizon_match_status"] == "exact"
    assert one_min["matched_snapshot_time"] == "2026-06-29T00:01:00Z"
    assert current["horizon_match_status"] == "current_latest"
    assert current["matched_snapshot_time"] == "2026-06-29T01:00:00Z"


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


def _valid_crypto_touch_candidate(**overrides):
    row = {
        "market_slug": "btc-touch",
        "token_id": "yes-token",
        "category": "crypto",
        "side": "YES",
        "model_source": "crypto_lognormal_threshold_model",
        "semantics_type": "touch_barrier",
        "probability_semantics": "touch_barrier",
        "market_creation_time": "2026-06-08T00:00:00Z",
        "high_since_start_verified": True,
        "barrier_already_touched": False,
        "max_high_since_start": 65000,
        "p_yes_touch": 0.4,
        "p_no_touch": 0.6,
        "p_trade_lcb": 0.32,
        "q_effective": 0.2,
        "paper_only": True,
        "live_order_path": False,
        "orderbook_snapshot": {"best_bid": 0.19, "best_ask": 0.2, "bid_ladder": [], "ask_ladder": []},
    }
    row.update(overrides)
    return row


def test_crypto_fill_requires_touch_semantics_fields():
    journal = build_probability_edge_fills_with_snapshots(
        candidates=[_valid_crypto_touch_candidate(market_creation_time=None)],
        recorded_at="2026-01-01T00:00:00Z",
    )

    assert journal["fills"] == []
    assert journal["rejected_fill_count"] == 1
    assert "missing_crypto_touch_fields" in journal["rejected_fills"][0]["reason"]


def test_crypto_fill_rejects_start_time_unverified():
    journal = build_probability_edge_fills_with_snapshots(
        candidates=[_valid_crypto_touch_candidate(high_since_start_verified=False)],
        recorded_at="2026-01-01T00:00:00Z",
    )

    assert journal["fills"] == []
    assert journal["rejected_fills"][0]["reason"] == "start_time_unverified_or_high_unverified"


def test_crypto_fill_rejects_barrier_already_touched():
    journal = build_probability_edge_fills_with_snapshots(
        candidates=[_valid_crypto_touch_candidate(barrier_already_touched=True)],
        recorded_at="2026-01-01T00:00:00Z",
    )

    assert journal["fills"] == []
    assert journal["rejected_fills"][0]["reason"] == "barrier_already_touched"


def test_valid_crypto_touch_fill_gets_orderbook_snapshot_id():
    journal = build_probability_edge_fills_with_snapshots(
        candidates=[_valid_crypto_touch_candidate()],
        recorded_at="2026-01-01T00:00:00Z",
    )

    assert journal["rejected_fill_count"] == 0
    assert journal["fills"][0]["orderbook_snapshot_id"]


def test_formal_fill_followup_snapshot_uses_current_orderbook():
    fill = _valid_crypto_touch_candidate(EV_safe=0.03, orderbook_snapshot_id="snap-1")
    fill["entry_time"] = "2026-06-29T00:00:00Z"
    report = build_formal_fill_followup_orderbook_snapshots(
        fills=[fill],
        active_markets=[
            {
                "market_slug": "btc-touch",
                "token_ids": ["yes-token"],
                "orderbooks": {"yes-token": {"best_bid": 0.21, "best_ask": 0.22, "spread": 0.01, "ask_depth_usdc_3c": 10}},
            }
        ],
        recorded_at="2026-06-29T00:05:00Z",
    )

    assert report["snapshot_count"] == 1
    snapshot = report["snapshots"][0]
    assert snapshot["source"] == "crypto_touch_formal_fill_followup"
    assert snapshot["fill_id"]
    assert snapshot["horizon_target"] == "60s"
    assert "60s" in snapshot["horizon_targets_reached"]
    assert "300s" in snapshot["horizon_targets_reached"]
    assert snapshot["live_order_path"] is False
