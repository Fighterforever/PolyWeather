from __future__ import annotations

from src.trading.polymarket_alpha.active_probability_edge_scanner import (
    build_formal_fill_followup_orderbook_snapshots,
    build_formal_fill_followup_snapshot_coverage_report,
    scan_active_probability_edges,
)
from scripts.polymarket_alpha_active_probability_edge_report import _merge_probability_edge_fills


def _market(category: str = "crypto", **overrides):
    row = {
        "category": category,
        "market_slug": "btc-up",
        "event_slug": "btc",
        "active": True,
        "token_id_by_outcome": {"Yes": "yes-token"},
        "orderbooks": {
            "yes-token": {
                "best_bid": 0.2,
                "best_ask": 0.25,
                "spread": 0.05,
                "ask_depth_usdc_3c": 100,
            }
        },
    }
    row.update(overrides)
    return row


def _model(oos_passed: bool = True):
    return {
        "validate_row_count": 10 if oos_passed else 0,
        "overall": {
            "brier_improvement": 0.1 if oos_passed else None,
            "log_loss_improvement": 0.1 if oos_passed else None,
        },
        "model": {
            "bins": {
                "global|0_15_0_35|missing|3c_7c": {
                    "p": 0.8,
                    "p_lcb": 0.75,
                    "p_ucb": 0.85,
                    "sample_count": 100,
                },
                "global|0_15_0_35|*|*": {
                    "p": 0.8,
                    "p_lcb": 0.75,
                    "p_ucb": 0.85,
                    "sample_count": 100,
                },
            }
        },
    }


def test_active_probability_edge_scanner_writes_candidate_when_oos_model_passes():
    report = scan_active_probability_edges(
        active_markets=[_market()],
        model_report=_model(True),
        min_edge=0.05,
        min_depth=10,
        max_spread=0.1,
        cost=0.01,
    )

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["model_source"] == "global_oos_probability_model"
    assert candidate["side"] == "YES"
    assert candidate["EV_safe"] > 0
    assert candidate["paper_only"] is True
    assert candidate["live_order_path"] is False


def test_active_probability_edge_scanner_blocks_when_global_oos_not_passed():
    report = scan_active_probability_edges(active_markets=[_market()], model_report=_model(False))

    blockers = {row["reason"]: row["count"] for row in report["blocker_counts"]}
    assert blockers["global_oos_model_not_passed"] == 1
    assert report["candidate_count"] == 0
    assert report["lane_reports"]["global_oos_model_lane"]["blocker_counts"][0]["reason"] == "global_oos_model_not_passed"
    assert report["lane_reports"]["crypto_model_lane"]["blocker_counts"] == []


def test_active_probability_edge_scanner_prioritizes_crypto_candidates():
    crypto_candidate = {
        "market_slug": "btc-over-100k",
        "token_id": "yes-token",
        "category": "crypto",
        "model_source": "crypto_lognormal_threshold_model",
        "EV_safe": 0.03,
        "paper_only": True,
        "live_order_path": False,
    }
    report = scan_active_probability_edges(
        active_markets=[_market(category="sports")],
        model_report=_model(False),
        crypto_report={"candidates": [crypto_candidate]},
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["model_source"] == "crypto_lognormal_threshold_model"
    assert report["by_model_source"] == [{"model_source": "crypto_lognormal_threshold_model", "count": 1}]
    assert report["lane_reports"]["crypto_model_lane"]["candidate_count"] == 1
    assert report["lane_reports"]["global_oos_model_lane"]["candidate_count"] == 0


def test_crypto_candidate_downgraded_when_surface_unsupported():
    crypto_candidate = {
        "market_slug": "btc-over-100k",
        "token_id": "yes-token",
        "side": "YES",
        "category": "crypto",
        "model_source": "crypto_lognormal_threshold_model",
        "EV_safe": 0.03,
        "paper_only": True,
        "live_order_path": False,
    }
    report = scan_active_probability_edges(
        active_markets=[],
        model_report=_model(False),
        crypto_report={"candidates": [crypto_candidate]},
        surface_report={"rows": [{**crypto_candidate, "group_member_count": 2, "surface_supports_model_direction": False}]},
        min_edge=0.01,
    )

    assert report["candidate_count"] == 0
    assert report["old_formal_fill_count"] == 1
    assert report["new_formal_fill_count"] == 0
    assert report["downgraded_due_surface_count"] == 1
    assert report["lane_reports"]["crypto_model_lane"]["top_watch_rows"][0]["downgrade_reasons"] == ["surface_unsupported"]


def test_crypto_candidate_downgraded_when_sensitivity_fragile_below_stricter_edge():
    crypto_candidate = {
        "market_slug": "btc-over-100k",
        "token_id": "yes-token",
        "side": "YES",
        "category": "crypto",
        "model_source": "crypto_lognormal_threshold_model",
        "EV_safe": 0.015,
        "paper_only": True,
        "live_order_path": False,
    }
    report = scan_active_probability_edges(
        active_markets=[],
        model_report=_model(False),
        crypto_report={"candidates": [crypto_candidate]},
        surface_report={"rows": [{**crypto_candidate, "group_member_count": 2, "surface_supports_model_direction": True}]},
        sensitivity_report={"rows": [{**crypto_candidate, "sensitivity_fragile": True}]},
        min_edge=0.01,
        stricter_edge=0.02,
    )

    assert report["candidate_count"] == 0
    assert report["downgraded_due_sensitivity_count"] == 1
    assert "sensitivity_fragile_below_stricter_edge" in report["watch_rows"][0]["downgrade_reasons"]


def test_crypto_candidate_allowed_when_surface_sparse_and_sensitivity_clean():
    crypto_candidate = {
        "market_slug": "btc-over-100k",
        "token_id": "yes-token",
        "side": "YES",
        "category": "crypto",
        "model_source": "crypto_lognormal_threshold_model",
        "EV_safe": 0.015,
        "paper_only": True,
        "live_order_path": False,
    }
    report = scan_active_probability_edges(
        active_markets=[],
        model_report=_model(False),
        crypto_report={"candidates": [crypto_candidate]},
        surface_report={"rows": [{**crypto_candidate, "group_member_count": 1, "surface_supports_model_direction": False}]},
        sensitivity_report={"rows": [{**crypto_candidate, "sensitivity_fragile": False}]},
        min_edge=0.01,
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["surface_group_too_sparse"] is True
    assert report["candidates"][0]["live_order_path"] is False


def test_active_probability_edge_scanner_keeps_politics_neutral_stats_only():
    report = scan_active_probability_edges(
        active_markets=[_market(category="politics")],
        model_report=_model(True),
        min_edge=0.05,
    )

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["neutral_political_stats_only"] is True


def test_active_scanner_crypto_lane_runs_even_when_global_oos_not_passed():
    report = scan_active_probability_edges(
        active_markets=[_market(category="sports")],
        model_report=_model(False),
        crypto_report={
            "parsed_crypto_market_count": 2,
            "model_ready_count": 2,
            "executable_price_available_count": 1,
            "candidate_count": 0,
            "blocker_counts": [{"reason": "ev_below_min", "count": 2}],
            "top_10_near_misses": [{"market_slug": "btc", "EV_safe": -0.01}],
        },
    )

    crypto_lane = report["lane_reports"]["crypto_model_lane"]
    global_lane = report["lane_reports"]["global_oos_model_lane"]
    assert crypto_lane["scanned_count"] == 2
    assert crypto_lane["model_ready_count"] == 2
    assert crypto_lane["executable_price_available_count"] == 1
    assert crypto_lane["blocker_counts"][0]["reason"] == "ev_below_min"
    assert global_lane["blocker_counts"][0]["reason"] == "global_oos_model_not_passed"


def test_active_scanner_reports_lane_specific_blockers():
    report = scan_active_probability_edges(
        active_markets=[_market()],
        model_report=_model(True),
        crypto_report={
            "parsed_crypto_market_count": 1,
            "model_ready_count": 1,
            "executable_price_available_count": 0,
            "gap_reasons": [{"reason": "yes_no_ask_depth", "count": 1}],
        },
        min_depth=1000,
    )

    crypto_blockers = {row["reason"]: row["count"] for row in report["lane_reports"]["crypto_model_lane"]["blocker_counts"]}
    global_blockers = {row["reason"]: row["count"] for row in report["lane_reports"]["global_oos_model_lane"]["blocker_counts"]}
    assert crypto_blockers["yes_no_ask_depth"] == 1
    assert global_blockers["depth_insufficient"] == 1


def test_active_scanner_crypto_missing_ask_not_global_oos():
    report = scan_active_probability_edges(
        active_markets=[_market(category="crypto")],
        model_report=_model(False),
        crypto_report={
            "parsed_crypto_market_count": 1,
            "model_ready_count": 1,
            "executable_price_available_count": 0,
            "blocker_counts": [{"reason": "yes_no_ask_depth", "count": 1}],
        },
    )

    crypto_blockers = {row["reason"]: row["count"] for row in report["lane_reports"]["crypto_model_lane"]["blocker_counts"]}
    assert crypto_blockers["yes_no_ask_depth"] == 1
    assert report["lane_reports"]["global_oos_model_lane"]["blocker_counts"][0]["reason"] == "global_oos_model_not_passed"


def test_no_side_ev_uses_no_lcb():
    market = _market(
        orderbooks={
            "yes-token": {
                "best_bid": 0.8,
                "best_ask": 0.85,
                "spread": 0.05,
                "ask_depth_usdc_3c": 100,
            }
        }
    )
    model = _model(True)
    model["model"]["bins"]["global|0_65_0_85|*|*"] = {"p": 0.2, "p_lcb": 0.15, "p_ucb": 0.25, "sample_count": 100}
    report = scan_active_probability_edges(active_markets=[market], model_report=model, min_edge=0.01, cost=0.01)

    candidate = report["candidates"][0]
    assert candidate["side"] == "NO"
    assert candidate["p_trade_lcb"] == candidate["p_no_lcb"]
    assert candidate["EV_safe"] == round(candidate["p_no_lcb"] - candidate["q_effective"] - candidate["cost"], 8)


def test_yes_side_ev_uses_yes_lcb():
    report = scan_active_probability_edges(active_markets=[_market()], model_report=_model(True), min_edge=0.05, cost=0.01)

    candidate = report["candidates"][0]
    assert candidate["side"] == "YES"
    assert candidate["p_trade_lcb"] == candidate["p_yes_lcb"]
    assert candidate["EV_safe"] == round(candidate["p_yes_lcb"] - candidate["q_effective"] - candidate["cost"], 8)


def test_fill_schema_includes_trade_probability_fields():
    crypto_candidate = {
        "market_slug": "btc-over-100k",
        "token_id": "yes-token",
        "category": "crypto",
        "model_source": "crypto_lognormal_threshold_model",
        "EV_safe": 0.03,
        "p_trade_lcb": 0.7,
        "p_yes_lcb": 0.7,
        "p_no_lcb": 0.2,
        "paper_only": True,
        "live_order_path": False,
    }
    report = scan_active_probability_edges(
        active_markets=[_market(category="sports")],
        model_report=_model(False),
        crypto_report={"candidates": [crypto_candidate]},
    )

    candidate = report["candidates"][0]
    assert candidate["p_trade_lcb"] == 0.7
    assert "p_yes_lcb" in candidate
    assert "p_no_lcb" in candidate


def test_active_scanner_exports_formal_fill_followup_snapshot_builder():
    report = build_formal_fill_followup_orderbook_snapshots(
        fills=[
            {
                "fill_id": "fill-1",
                "market_slug": "btc-touch",
                "token_id": "yes-token",
                "side": "YES",
                "semantics_type": "touch_barrier",
                "probability_semantics": "touch_barrier",
                "high_since_start_verified": True,
                "barrier_already_touched": False,
                "orderbook_snapshot_id": "entry-snap",
                "EV_safe": 0.03,
                "entry_time": "2026-06-29T00:00:00Z",
                "q_effective": 0.2,
                "paper_only": True,
                "live_order_path": False,
            }
        ],
        active_markets=[
            {
                "market_slug": "btc-touch",
                "token_ids": ["yes-token"],
                "orderbooks": {"yes-token": {"best_bid": 0.21, "best_ask": 0.22, "spread": 0.01, "ask_depth_usdc_3c": 20}},
            }
        ],
        recorded_at="2026-06-29T00:05:00Z",
    )

    assert report["snapshot_count"] == 1
    assert report["snapshots"][0]["source"] == "crypto_touch_formal_fill_followup"
    assert report["snapshots"][0]["paper_only"] is True


def test_active_report_merge_preserves_existing_formal_fills_when_no_new_candidate():
    existing = [
        {
            "fill_id": "fill-1",
            "market_slug": "btc-touch",
            "token_id": "yes-token",
            "side": "YES",
            "timestamp": "2026-06-29T00:00:00Z",
            "paper_only": True,
            "live_order_path": False,
        }
    ]

    assert _merge_probability_edge_fills(existing, []) == existing


def test_active_report_merge_dedupes_repeated_token_fill_and_keeps_first_entry():
    existing = [
        {
            "fill_id": "first",
            "market_slug": "btc-touch",
            "token_id": "yes-token",
            "side": "YES",
            "timestamp": "2026-06-29T00:00:00Z",
        }
    ]
    repeated = [
        {
            "fill_id": "later",
            "market_slug": "btc-touch",
            "token_id": "yes-token",
            "side": "YES",
            "timestamp": "2026-06-29T00:01:00Z",
        }
    ]

    merged = _merge_probability_edge_fills(existing, repeated)

    assert len(merged) == 1
    assert merged[0]["fill_id"] == "first"


def test_followup_snapshot_coverage_reports_missing_horizons():
    report = build_formal_fill_followup_snapshot_coverage_report(
        fills=[
            {
                "fill_id": "fill-1",
                "market_slug": "btc-touch",
                "token_id": "yes-token",
                "entry_time": "2026-06-29T00:00:00Z",
            }
        ],
        followup_snapshots=[
            {
                "fill_id": "fill-1",
                "token_id": "yes-token",
                "recorded_at": "2026-06-29T00:05:30Z",
                "best_bid": 0.2,
            }
        ],
        watcher_status={"state": "running"},
    )

    assert report["fill_count"] == 1
    assert report["followup_snapshot_count_by_fill"][0]["count"] == 1
    assert report["coverage_by_horizon"]["5m"]["covered_fill_count"] == 1
    assert report["coverage_by_horizon"]["60s"]["missing_fill_count"] == 1
    assert report["missing_horizon_by_fill"][0]["missing_horizons"] == ["60s", "15m", "1h", "6h", "24h"]
    assert report["watcher_status"]["state"] == "running"
