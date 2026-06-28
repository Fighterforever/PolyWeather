from __future__ import annotations

from src.trading.polymarket_alpha.probability_edge_model import build_probability_edge_model_report


def _row(event: str, ts: str, price: float, payout: float):
    return {
        "market_slug": event,
        "event_slug": event,
        "category": "crypto",
        "token_id": f"{event}-yes",
        "timestamp": ts,
        "price_mid": price,
        "best_ask": None,
        "best_bid": None,
        "resolved_payout": payout,
        "resolved": True,
        "price_bucket": "0_15_0_35",
        "executable_depth_available": False,
        "paper_only": True,
        "live_order_path": False,
    }


def test_probability_edge_model_beats_market_oos_with_proxy_candidate_flag():
    rows = [
        _row("e1", "2026-01-01T00:00:00Z", 0.2, 1.0),
        _row("e2", "2026-01-02T00:00:00Z", 0.2, 1.0),
        _row("e3", "2026-01-03T00:00:00Z", 0.2, 1.0),
        _row("e4", "2026-01-04T00:00:00Z", 0.2, 1.0),
    ]

    report = build_probability_edge_model_report(rows, min_edge=0.1, cost=0.01, train_fraction=0.5)

    assert report["train_row_count"] == 2
    assert report["validate_row_count"] == 2
    assert report["overall"]["brier_improvement"] > 0
    assert report["candidate_count"] == 2
    assert all(row["proxy_only"] is True for row in report["candidates"])
    assert all(row["live_order_path"] is False for row in report["candidates"])


def test_probability_edge_model_keeps_unprofitable_market_baseline_out():
    rows = [
        _row("e1", "2026-01-01T00:00:00Z", 0.5, 1.0),
        _row("e2", "2026-01-02T00:00:00Z", 0.5, 0.0),
        _row("e3", "2026-01-03T00:00:00Z", 0.5, 1.0),
    ]

    report = build_probability_edge_model_report(rows, min_edge=0.2, cost=0.01)

    assert report["candidate_count"] == 0
    assert report["live_order_path"] is False


def test_probability_edge_model_reports_no_leakage_safe_oos_blocker_for_single_event_category():
    rows = [
        _row("same-event", "2026-01-01T00:00:00Z", 0.2, 1.0),
        _row("same-event", "2026-01-01T01:00:00Z", 0.25, 1.0),
    ]

    report = build_probability_edge_model_report(rows)

    assert report["validate_row_count"] == 0
    assert report["hard_conclusion"] == "no_probability_edge_yet_insufficient_category_event_oos_split"
    assert report["oos_split_blocker_counts"]["single_resolved_event_category_count"] == 1
