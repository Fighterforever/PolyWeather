from __future__ import annotations

from src.trading.weather_eq_dead_no_markout import build_eq_dead_no_markout_report


def test_eq_dead_no_markout_waits_for_paper_fills_when_empty():
    report = build_eq_dead_no_markout_report(fills=[], orderbook_snapshots=[])

    assert report["status"] == "ready_waiting_for_paper_fills"
    assert report["fill_count"] == 0
    assert report["mean_markout_cents"] is None


def test_eq_dead_no_markout_uses_no_bid_after_fill():
    report = build_eq_dead_no_markout_report(
        fills=[{"fill_id": "fill-1", "token_id": "no-token", "recorded_at": "2026-06-29T10:00:00Z", "entry_price": 0.8}],
        orderbook_snapshots=[
            {"token_id": "no-token", "recorded_at": "2026-06-29T10:01:00Z", "best_bid": 0.84},
            {"token_id": "no-token", "recorded_at": "2026-06-29T10:03:00Z", "best_bid": 0.85},
            {"token_id": "no-token", "recorded_at": "2026-06-29T10:05:00Z", "best_bid": 0.86},
            {"token_id": "no-token", "recorded_at": "2026-06-29T10:15:00Z", "best_bid": 0.87},
        ],
    )

    assert report["status"] == "evaluated"
    assert report["available_markout_count"] == 4
    assert report["rows"][0]["exit_no_bid"] == 0.84
    assert report["rows"][0]["markout_cents"] == 4.0
