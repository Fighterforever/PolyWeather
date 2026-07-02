from __future__ import annotations

from src.trading.polymarket_alpha.crypto_touch_watch_markout import build_crypto_touch_watch_markout_report


def _watch(**overrides):
    row = {
        "watch_id": "watch-1",
        "market_slug": "btc-touch",
        "token_id": "yes-token",
        "asset": "BTC",
        "side": "YES",
        "entry_time": "2026-06-29T00:00:00Z",
        "recorded_at": "2026-06-29T00:00:00Z",
        "orderbook_snapshot_id": "snapshot-1",
        "q_effective": 0.27,
        "paper_only": True,
        "live_order_path": False,
    }
    row.update(overrides)
    return row


def test_crypto_touch_watch_markout_uses_later_snapshot():
    report = build_crypto_touch_watch_markout_report(
        watch_rows=[_watch()],
        orderbook_snapshots=[
            {"token_id": "yes-token", "timestamp": "2026-06-29T00:05:00Z", "best_bid": 0.31},
        ],
        horizons=(300,),
    )

    assert report["available_markout_count"] == 1
    assert report["markouts"][0]["markout_cents"] == 4.0
    assert report["markout_status"] == "markout_available"
    assert report["by_horizon"][0]["mean_markout_cents"] == 4.0
    assert report["by_asset"][0]["mean_markout_cents"] == 4.0
    assert report["by_side"][0]["mean_markout_cents"] == 4.0
    assert report["live_order_path"] is False


def test_crypto_touch_watch_markout_reports_missing_snapshot():
    report = build_crypto_touch_watch_markout_report(watch_rows=[_watch()], orderbook_snapshots=[], horizons=(300,))

    assert report["available_markout_count"] == 0
    assert report["markouts"][0]["missing_snapshot_reason"] == "missing_later_snapshot"
    assert report["markout_status"] == "missing_later_snapshots"


def test_crypto_touch_watch_markout_handles_empty_watch():
    report = build_crypto_touch_watch_markout_report(watch_rows=[], orderbook_snapshots=[], horizons=(300,))

    assert report["watch_count"] == 0
    assert report["markout_status"] == "no_near_miss_watch_rows"


def test_near_miss_markout_uses_entry_time():
    report = build_crypto_touch_watch_markout_report(
        watch_rows=[_watch(entry_time="2026-06-29T00:10:00Z", recorded_at="2026-06-29T00:00:00Z")],
        orderbook_snapshots=[
            {"token_id": "yes-token", "recorded_at": "2026-06-29T00:05:00Z", "best_bid": 0.50},
            {"token_id": "yes-token", "recorded_at": "2026-06-29T00:15:00Z", "best_bid": 0.31},
        ],
        horizons=(300,),
    )

    assert report["available_markout_count"] == 1
    assert report["markouts"][0]["exit_time"] == "2026-06-29T00:15:00Z"
    assert report["markouts"][0]["markout_cents"] == 4.0


def test_near_miss_not_counted_as_fill():
    report = build_crypto_touch_watch_markout_report(watch_rows=[_watch()], orderbook_snapshots=[], horizons=(300,))

    assert report["counts_for_live_gate"] is False
    assert all(row["live_order_path"] is False for row in report["markouts"])
