from __future__ import annotations

from src.trading.polymarket_alpha.probability_edge_model import build_probability_edge_model_report


def _row(event: str, ts: str, price: float, payout: float, *, category: str = "crypto", executable: bool = True):
    return {
        "decision_snapshot_id": f"{event}|yes|{ts}",
        "event_family_id": event,
        "market_slug": event,
        "event_slug": event,
        "category": category,
        "token_id": f"{event}-yes",
        "decision_time": ts,
        "timestamp": ts,
        "time_to_close_seconds": 86400,
        "price_mid": price,
        "best_ask": price + 0.01 if executable else None,
        "best_bid": price - 0.01 if executable else None,
        "depth": 100 if executable else None,
        "spread": 0.02 if executable else None,
        "volume": 1000,
        "resolved_payout": payout,
        "resolved": True,
        "price_bucket": "0_15_0_35" if price < 0.35 else "0_35_0_65",
        "spread_bucket": "1c_3c" if executable else "missing",
        "time_to_close_bucket": "1d_7d",
        "executable_depth_available": executable,
        "paper_only": True,
        "live_order_path": False,
    }


def test_leave_family_out_no_leakage():
    rows = [
        _row("f1", "2026-01-01T00:00:00Z", 0.2, 1.0),
        _row("f2", "2026-01-02T00:00:00Z", 0.2, 1.0),
        _row("f3", "2026-01-03T00:00:00Z", 0.2, 1.0),
        _row("f4", "2026-01-04T00:00:00Z", 0.2, 1.0),
    ]

    report = build_probability_edge_model_report(rows, min_edge=0.1, cost=0.01, train_fraction=0.5)

    assert set(report["split"]["train_families"]).isdisjoint(set(report["split"]["validate_families"]))
    assert report["train_row_count"] == 2
    assert report["validate_row_count"] == 2
    assert report["unique_resolved_event_family_count"] == 4
    assert report["overall"]["brier_improvement"] > 0


def test_global_model_can_score_category_with_single_family():
    rows = [
        _row("train-a", "2026-01-01T00:00:00Z", 0.2, 1.0, category="sports"),
        _row("train-b", "2026-01-02T00:00:00Z", 0.2, 1.0, category="finance"),
        _row("validate-crypto", "2026-01-03T00:00:00Z", 0.2, 1.0, category="crypto"),
    ]

    report = build_probability_edge_model_report(rows, min_edge=0.1, cost=0.01, train_fraction=0.67)

    crypto = next(row for row in report["by_category"] if row["category"] == "crypto")
    assert crypto["sample_count"] == 1
    assert report["validate_row_count"] == 1
    assert report["hard_conclusion"] == "probability_edge_candidate_categories_found"


def test_mid_price_training_filter():
    rows = [
        _row("f1", "2026-01-01T00:00:00Z", 0.02, 1.0),
        _row("f2", "2026-01-02T00:00:00Z", 0.2, 1.0),
        _row("f3", "2026-01-03T00:00:00Z", 0.98, 1.0),
    ]

    report = build_probability_edge_model_report(rows)

    assert report["input_resolved_row_count"] == 3
    assert report["mid_price_training_row_count"] == 1


def test_extreme_price_not_candidate():
    rows = [
        _row("f1", "2026-01-01T00:00:00Z", 0.2, 1.0),
        _row("f2", "2026-01-02T00:00:00Z", 0.2, 1.0),
        _row("f3", "2026-01-03T00:00:00Z", 0.96, 1.0),
    ]

    report = build_probability_edge_model_report(rows, min_edge=0.01)

    assert all(row["market_price"] < 0.95 for row in report["candidates"])


def test_oos_report_has_brier_and_logloss():
    rows = [
        _row("f1", "2026-01-01T00:00:00Z", 0.4, 1.0),
        _row("f2", "2026-01-02T00:00:00Z", 0.4, 1.0),
        _row("f3", "2026-01-03T00:00:00Z", 0.4, 1.0),
    ]

    report = build_probability_edge_model_report(rows)

    assert report["overall"]["brier_market"] is not None
    assert report["overall"]["brier_model"] is not None
    assert report["overall"]["log_loss_market"] is not None
    assert report["overall"]["log_loss_model"] is not None
    assert report["live_order_path"] is False
