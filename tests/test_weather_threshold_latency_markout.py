from __future__ import annotations

from src.trading.weather_threshold_latency_markout import build_threshold_latency_markout_report


def _fill():
    return {
        "fill_id": "fill-1",
        "token_id": "token-1",
        "recorded_at": "2026-06-29T10:00:00Z",
        "entry_price": 0.2,
    }


def test_threshold_latency_markout_uses_later_bid_snapshot():
    report = build_threshold_latency_markout_report(
        fills=[_fill()],
        orderbook_snapshots=[
            {"token_id": "token-1", "recorded_at": "2026-06-29T10:01:00Z", "best_bid": 0.25},
        ],
    )

    one_minute = [row for row in report["rows"] if row["horizon"] == "1m"][0]
    assert one_minute["exit_bid"] == 0.25
    assert one_minute["markout_cents"] == 5.0
    assert report["available_markout_count"] == 1


def test_threshold_latency_markout_reports_missing_snapshot_without_faking_markout():
    report = build_threshold_latency_markout_report(fills=[_fill()], orderbook_snapshots=[])

    assert report["available_markout_count"] == 0
    assert report["mean_markout_cents"] is None
    assert all(row["markout_cents"] is None for row in report["rows"])
    assert all(row["missing_snapshot_reason"] == "missing_later_orderbook_snapshot" for row in report["rows"])
